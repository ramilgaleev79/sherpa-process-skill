"""plan.json (таблица шагов) → IR (JSON) со строгой валидацией и автоматической проводкой.

Идея: граф собирается не вручную и не по паттерну, а механически из таблицы шагов.
Каждый шаг плана несёт цитату задачи; типы шагов задают конструкцию; порты
соединяются по подтверждённым правилам (тело цикла → InputLeft … возврат в Input).

Схема плана
-----------
{
  "process": {"name": "Screenshots5"},
  "variables": [
    {"name": "Индекс", "sherpa_type": "Number", "initial": 0},
    {"name": "Файл", "sherpa_type": "Text", "initial": ""}
  ],
  "steps": [ <шаг>, ... ]          # последовательность верхнего уровня
}

<шаг> — один из:
  action    {"id","quote","type":"action","block":"MakeScreenshot","props":{...},"display":"...","tech":true?,
             "on_error":[<шаги>]?}         — обработка ошибки блока через его выход Error
            либо {"type":"action","todo":"Сделать снимок экрана"} для отсутствующего блока
  condition {"id","quote","type":"condition","expr":{IR-выражение},"then":[<шаги>],"else":[<шаги>]}
  repeat    {"id","quote","type":"repeat","kind":"for|while|foreach_rows|foreach_list", ...,
             "body":[<шаги>]}
              for:          start, end, step, index
              while:        expr
              foreach_rows: table, row, index?
              foreach_list: items, item
  try       {"id","quote","type":"try","attempts":3,"pause":1,"body":[<шаги>],"on_error":[<шаги>]?}
  terminal  {"id","quote","type":"terminal"}          # Stop
  data      {"id","quote","type":"data","declare":[{"name","sherpa_type","initial"?}]}

Правила проводки (подтверждены по корпусу установки)
----------------------------------------------------
  For / Foreach*:  вход тела — Index/Row/Item → первый блок тела (InputLeft);
                   последний блок тела → <цикл>.Input; выход из цикла — Next.
  While:           вход тела — Iteration; возврат в While.Input; выход — Next.
  Condition:       Yes/No → первая строка ветки (или продолжение, если ветка пуста).
  TryCatch:        Next → тело; тело → продолжение; Out → продолжение; Error → on_error.
  Terminal:        Stop, обязан быть последним в своей последовательности.

Коды ошибок плана: PLAN-001 … PLAN-012 (см. PlanError).

Запуск
------
  python scripts/dev/plan_to_ir.py plan.json out.ir.json          # plan → IR
  python scripts/dev/plan_to_ir.py plan.json --check              # только проверки
  python scripts/dev/plan_to_ir.py plan.json out.ir.json --print-graph

Дальше — обычная сборка: python scripts/cli.py build out.ir.json out.process --mode release
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from sherpa_xml import ir as irlib, validator  # noqa: E402
from sherpa_xml.compiler import Compiler, ps_expression  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402

STEP_TYPES = {"action", "condition", "repeat", "try", "terminal", "data"}
REPEAT_KINDS = {"for", "while", "foreach_rows", "foreach_list"}
VAR_REF = re.compile(r"\$([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)")


def _block_ports(physical: str) -> list[str]:
    """Порты блока по каталогу (assets/block_index.json) — для проверки on_error."""
    entry = irlib.BLOCK_INDEX.get("blocks", {}).get(physical) or {}
    return list(entry.get("ports") or [])


class PlanError(ValueError):
    """Нарушение схемы плана; code — машинный идентификатор."""

    def __init__(self, code: str, where: str, message: str):
        super().__init__(f"{code} {where}: {message}")
        self.code, self.where, self.message = code, where, message


class PlanCompiler:
    def __init__(self, plan: dict, strict: bool = True):
        self.plan = plan
        self.strict = strict
        self.templates = Compiler().templates
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.variables: list[dict] = []
        self.declared: set[str] = set()
        self.row_map: list[dict] = []
        self._ids: set[str] = set()
        self._index_vars: set[str] = set()

    # ---------------------------------------------------------------- утилиты
    def _err(self, code: str, where: str, message: str) -> None:
        raise PlanError(code, where, message)

    def _step_id(self, step: dict, where: str) -> str:
        sid = step.get("id")
        if not sid or not isinstance(sid, str):
            self._err("PLAN-001", where, "у шага нет строкового id")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sid):
            self._err("PLAN-001", where, f"id {sid!r} должен быть латиницей/цифрами/подчёркиванием")
        if sid in self._ids:
            self._err("PLAN-001", where, f"дублирующийся id {sid!r}")
        self._ids.add(sid)
        return sid

    def _quote(self, step: dict, where: str) -> str:
        quote = (step.get("quote") or "").strip()
        if not quote:
            self._err("PLAN-002", where, "нет цитаты задачи (quote) — строка без источника запрещена")
        return quote

    def _expr(self, raw: object, where: str) -> dict:
        if not isinstance(raw, dict):
            self._err("PLAN-004", where, "выражение должно быть объектом {kind: ...}")

        def deep(obj: object) -> object:
            if isinstance(obj, dict):
                out = {k: deep(v) for k, v in obj.items()}
                if "kind" in out:
                    try:
                        return irlib.Expr(out)
                    except (irlib.IRError, ValueError) as exc:
                        self._err("PLAN-004", where, f"некорректное выражение: {exc}")
                return out
            if isinstance(obj, list):
                return [deep(x) for x in obj]
            return obj

        return dict(deep(raw))

    def _var(self, name: object, where: str, allow_external: bool = False) -> str:
        if not isinstance(name, str) or not name:
            self._err("PLAN-009", where, "ожидается имя переменной")
        if name not in self.declared and not allow_external:
            self._err("PLAN-009", where, f"переменная {name!r} не объявлена в variables")
        return name

    # ---------------------------------------------------------------- переменные
    def compile_variables(self) -> None:
        variables = self.plan.get("variables") or []
        if not isinstance(variables, list):
            self._err("PLAN-010", "variables", "ожидается список")
        for i, v in enumerate(variables, start=1):
            where = f"variables[{i}]"
            if not isinstance(v, dict):
                self._err("PLAN-010", where, "переменная должна быть объектом")
            name = v.get("name")
            if not name or not isinstance(name, str):
                self._err("PLAN-010", where, "нет имени")
            if name in self.declared:
                self._err("PLAN-010", where, f"дублирующееся имя {name!r}")
            vtype = v.get("sherpa_type")
            if vtype not in irlib.VARIABLE_TYPES:
                self._err("PLAN-010", where, f"неизвестный тип {vtype!r}")
            initial = v.get("initial")
            if initial is not None and not isinstance(initial, dict):
                initial = self._shorthand_initial(vtype, initial, where)
            self.variables.append({
                "key": f"v_{i}",
                "name": name,
                "sherpa_type": vtype,
                "scope": v.get("scope", "local"),
                "initial": initial,
            })
            self.declared.add(name)

    def _shorthand_initial(self, vtype: str, value: object, where: str) -> dict:
        if vtype == "Number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return {"kind": "number_literal", "value": value}
        if vtype == "Logical" and isinstance(value, bool):
            return {"kind": "boolean_literal", "value": value}
        if vtype == "SecureString":
            if value == "":
                return {"kind": "string_literal", "value": ""}
            self._err("PLAN-010", where, "SecureString объявляется только пустым")
        if vtype == "DateTime" and isinstance(value, str):
            if value == "":
                return {"kind": "string_literal", "value": ""}
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}", value):
                return {"kind": "datetime_literal", "value": value}
            self._err("PLAN-010", where, "DateTime задаётся как dd.MM.yyyy HH:mm:ss или пустой строкой")
        if vtype in ("Object", "Money") and isinstance(value, str):
            if value != "":
                self._err("PLAN-010", where, f"непустой {vtype}.initial не подтверждён — объявите пустым")
            return {"kind": "string_literal", "value": ""}
        if vtype == "Text" and isinstance(value, str):
            return {"kind": "string_literal", "value": value}
        if vtype in ("List", "Hashtable", "DataTable"):
            self._err("PLAN-010", where, f"для {vtype} задайте initial объектом-литералом (list_literal/…)")
        self._err("PLAN-010", where, f"initial не соответствует типу {vtype}")

    # ---------------------------------------------------------------- шаги
    def _add_node(self, node: dict) -> None:
        self.nodes.append(node)

    def _edge(self, src: str, sport: str, dst: str, dport: str = "Input") -> None:
        edge = {"from": src, "from_port": sport, "to": dst, "to_port": dport}
        if edge not in self.edges:
            self.edges.append(edge)

    def compile_step(self, step: dict, where: str) -> dict:
        """Создаёт узел (или узлы) шага, не связывая его с соседями."""
        if not isinstance(step, dict):
            self._err("PLAN-003", where, "шаг должен быть объектом")
        stype = step.get("type")
        if stype not in STEP_TYPES:
            self._err("PLAN-003", where, f"неизвестный тип шага {stype!r}; допустимо {sorted(STEP_TYPES)}")
        sid = self._step_id(step, where)
        quote = self._quote(step, where)
        tech = bool(step.get("tech"))
        row = {"id": sid, "quote": quote, "type": stype, "tech": tech, "nodes": [sid]}

        if stype == "action":
            if step.get("todo"):
                node = {"key": sid, "type": "ToDo", "unresolved_action": str(step["todo"])}
                row["block"] = "ToDo"
            else:
                block = step.get("block")
                if not block or not isinstance(block, str):
                    self._err("PLAN-005", where, "action без block (или без todo)")
                physical = irlib.resolve_physical(block)
                if physical is None:
                    self._err("PLAN-005", where, f"блока {block!r} нет в библиотеке — используйте todo")
                props = step.get("props") or {}
                if not isinstance(props, dict):
                    self._err("PLAN-007", where, "props должен быть объектом")
                props = self._check_props(physical, props, where)
                node = {"key": sid, "type": block}
                if props:
                    node["properties"] = props
                row["block"] = physical
                # обработка ошибки самого блока: Error-порт → свой сценарий
                # (graph-synthesis, шаг 5: «сообщить об ошибке» → Error → SendMailMessage/Log)
                if step.get("on_error") is not None:
                    if "Error" not in _block_ports(physical):
                        self._err("PLAN-008", where,
                                  f"у блока {physical} нет выхода Error — on_error неприменим")
                    if not isinstance(step["on_error"], list) or not step["on_error"]:
                        self._err("PLAN-006", where, "on_error должен быть непустым списком шагов")
            if step.get("display"):
                node["display_name"] = str(step["display"])

        elif stype == "condition":
            expr = self._expr(step.get("expr"), where)
            node = {"key": sid, "type": "Condition", "condition": expr}
            node["display_name"] = step.get("display") or self._expr_display(expr)
            row["block"] = "IfCondition"

        elif stype == "repeat":
            kind = step.get("kind")
            if kind not in REPEAT_KINDS:
                self._err("PLAN-006", where, f"kind повтора {kind!r}; допустимо {sorted(REPEAT_KINDS)}")
            if not step.get("body"):
                self._err("PLAN-006", where, "у повтора пустое тело (body)")
            if kind == "for":
                start = self._int(step.get("start", 1), where, "start")
                end = self._int(step.get("end"), where, "end")
                stepv = self._int(step.get("step", 1), where, "step")
                index = self._var(step.get("index"), where)
                self._index_vars.add(index)
                node = {"key": sid, "type": "ForLoop",
                        "start_index": {"kind": "number_literal", "value": start},
                        "end_index": {"kind": "number_literal", "value": end},
                        "step": {"kind": "number_literal", "value": stepv},
                        "index_variable": index}
                row["block"] = "For"
            elif kind == "while":
                node = {"key": sid, "type": "WhileLoop",
                        "condition": self._expr(step.get("expr"), where)}
                node["display_name"] = step.get("display") or self._expr_display(node["condition"])
                row["block"] = "While"
            elif kind == "foreach_rows":
                props = {"Table": "$" + self._var(step.get("table"), where),
                         "Row": "$" + self._var(step.get("row"), where)}
                if step.get("index"):
                    props["RowIndex"] = "$" + self._var(step["index"], where)
                self._index_vars.add(step.get("row"))
                node = {"key": sid, "type": "ForeachRows", "properties": props}
                row["block"] = "ForeachRows"
            else:  # foreach_list
                props = {"Items": "$" + self._var(step.get("items"), where),
                         "Item": "$" + self._var(step.get("item"), where)}
                node = {"key": sid, "type": "ForeachList", "properties": props}
                row["block"] = "ForeachList"

        elif stype == "try":
            if not step.get("body"):
                self._err("PLAN-006", where, "у try пустое тело (body)")
            node = {"key": sid, "type": "TryCatch"}
            props = {}
            if step.get("attempts") is not None:
                props["NumberAttempts"] = str(self._int(step["attempts"], where, "attempts"))
            if step.get("pause") is not None:
                props["PauseBetweenAttempts"] = str(self._int(step["pause"], where, "pause"))
            if props:
                node["properties"] = props
            row["block"] = "TryCatch"

        elif stype == "terminal":
            node = {"key": sid, "type": "Stop"}
            row["block"] = "ReturnOrStop"

        else:  # data
            declare = step.get("declare")
            if not isinstance(declare, list) or not declare:
                self._err("PLAN-010", where, "data-шаг без declare[]")
            for j, v in enumerate(declare, start=1):
                if not isinstance(v, dict) or not v.get("name"):
                    self._err("PLAN-010", f"{where}.declare[{j}]", "нет имени")
                name = v["name"]
                if name in self.declared:
                    self._err("PLAN-010", f"{where}.declare[{j}]", f"переменная {name!r} уже объявлена")
                vtype = v.get("sherpa_type")
                if vtype not in irlib.VARIABLE_TYPES:
                    self._err("PLAN-010", f"{where}.declare[{j}]", f"неизвестный тип {vtype!r}")
                initial = v.get("initial")
                if initial is not None and not isinstance(initial, dict):
                    initial = self._shorthand_initial(vtype, initial, f"{where}.declare[{j}]")
                self.variables.append({"key": f"v_{len(self.variables) + 1}", "name": name,
                                       "sherpa_type": vtype, "scope": v.get("scope", "local"),
                                       "initial": initial})
                self.declared.add(name)
            node = None
            row["block"] = "—"

        if node is not None:
            self._add_node(node)
        self.row_map.append(row)
        return {"id": sid, "type": stype, "node": node, "step": step}

    def _int(self, value: object, where: str, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            self._err("PLAN-006", where, f"{field} должен быть целым числом")
        return value

    def _check_props(self, physical: str, props: dict, where: str) -> dict:
        template = self.templates.get(physical)
        allowed = set()
        if template is not None:
            import xml.etree.ElementTree as ET
            for p in ET.fromstring(template["content"]).iter():
                if p.tag.split("}")[-1] == "Property":
                    allowed.add(p.get("Name", ""))
        out = {}
        for name, value in props.items():
            if not isinstance(value, str):
                self._err("PLAN-007", where, f"props.{name} должно быть строкой (числа — '3')")
            if template is not None and name not in allowed:
                self._err("PLAN-008", where, f"свойства {name!r} нет у блока {physical}")
            for ref in VAR_REF.findall(value):
                if ref not in self.declared:
                    self._err("PLAN-009", where, f"в props.{name} используется необъявленная ${ref}")
            out[name] = value
        return out

    @staticmethod
    def _expr_display(expr: dict) -> str:
        try:
            return ps_expression(expr)
        except Exception:  # noqa: BLE001 — отображение не критично
            return "условие"

    # ---------------------------------------------------------------- проводка
    def wire_sequence(self, steps: list[dict], where: str, follow: tuple[str, str] | None,
                      entry: tuple[str, str] | None = None) -> str | None:
        """Собирает последовательность: возвращает ключ первого узла.

        follow — куда идёт продолжение последнего шага (None допустим только для
        последовательностей, завершающихся терминалом).
        entry — порт входа первого узла (по умолчанию Input).
        """
        if not steps:
            return None
        compiled = [self.compile_step(step, f"{where}[{i}]") for i, step in enumerate(steps)]
        compiled = [c for c in compiled if c["node"] is not None]

        for i, item in enumerate(compiled):
            step = item["step"]
            node = item["node"]
            nxt = compiled[i + 1]["id"] if i + 1 < len(compiled) else None
            cont = (nxt, "Input") if nxt else follow
            stype = item["type"]

            if stype == "action":
                if step.get("is_terminal"):
                    self._err("PLAN-012", where, "terminal-шаг делайте типом terminal")
                if cont is None:
                    self._err("PLAN-012", f"{where}[{i}]", "шаг без продолжения и без терминала после него")
                self._edge(node["key"], "Next", cont[0], cont[1])
                handler = step.get("on_error") or []
                if handler:
                    hfirst = self.wire_sequence(handler, f"{where}[{i}].on_error", cont)
                    if hfirst is not None:
                        self._edge(node["key"], "Error", hfirst, "InputLeft")

            elif stype == "condition":
                for port, key in (("Yes", "then"), ("No", "else")):
                    branch = step.get(key) or []
                    if branch:
                        first = self.wire_sequence(branch, f"{where}[{i}].{key}", cont)
                        # wire_sequence возвращает ключ первого узла (строку)
                        entry_port = "Input" if port == "Yes" else "InputLeft"
                        target = (first, entry_port) if isinstance(first, str) else cont
                        if target is None:
                            self._err("PLAN-012", f"{where}[{i}].{key}", "ветка не завершается терминалом")
                        self._edge(node["key"], port, target[0], target[1])
                    else:
                        if cont is None:
                            self._err("PLAN-012", f"{where}[{i}].{key}", "пустая ветка без продолжения")
                        self._edge(node["key"], port, cont[0],
                                   "InputLeft" if port == "No" else cont[1])

            elif stype == "repeat":
                kind = step["kind"]
                body_port = {"for": "Index", "while": "Iteration",
                             "foreach_rows": "Row", "foreach_list": "Item"}[kind]
                first = self.wire_sequence(step["body"], f"{where}[{i}].body", (node["key"], "Input"))
                if first is None:
                    self._err("PLAN-006", f"{where}[{i}]", "тело повтора пустое после компиляции")
                self._edge(node["key"], body_port, first, "InputLeft")
                if cont is None:
                    self._err("PLAN-012", f"{where}[{i}]", "у повтора нет продолжения — нужен terminal")
                self._edge(node["key"], "Next", cont[0], cont[1])

            elif stype == "try":
                first = self.wire_sequence(step["body"], f"{where}[{i}].body", cont)
                if first is None:
                    self._err("PLAN-006", f"{where}[{i}]", "тело try пустое")
                self._edge(node["key"], "Next", first, "Input")
                if cont is not None:
                    self._edge(node["key"], "Out", cont[0], cont[1])
                handler = step.get("on_error") or []
                if handler:
                    hfirst = self.wire_sequence(handler, f"{where}[{i}].on_error", cont)
                    if hfirst is not None:
                        self._edge(node["key"], "Error", hfirst, "InputLeft")

            elif stype == "terminal":
                if i + 1 < len(compiled):
                    self._err("PLAN-012", f"{where}[{i}]", "после terminal в той же последовательности есть шаги")
                if entry is not None and i == 0:
                    pass

        first = compiled[0]["id"] if compiled else None
        if entry is not None and first is not None:
            # вход последовательности подключает вызывающая сторона (старт/порт цикла)
            pass
        return first

    # ---------------------------------------------------------------- сборка
    def compile(self) -> dict:
        self.compile_variables()
        steps = self.plan.get("steps")
        if not isinstance(steps, list) or not steps:
            self._err("PLAN-011", "steps", "план без шагов")

        has_terminal = any(isinstance(s, dict) and s.get("type") == "terminal" for s in steps)
        tail: list[dict] = []
        if not has_terminal:
            tail = [{"id": "__stop", "quote": "конец процесса", "type": "terminal"}]
        first = self.wire_sequence(steps + tail, "steps", None)
        if first is None:
            self._err("PLAN-011", "steps", "после компиляции не осталось шагов")

        stop_key = None
        for n in self.nodes:
            if n["type"] == "Stop":
                stop_key = n["key"]
                break
        if stop_key is None:
            self._err("PLAN-011", "steps", "в плане нет терминального шага")

        # Start → первый шаг
        start = {"key": "start_1", "type": "Start"}
        self.nodes.insert(0, start)
        self._edge("start_1", "Next", first, "Input")

        # проверки IR-контракта и портов — до выдачи файла
        data = {
            "process": {"name": (self.plan.get("process") or {}).get("name", "Plan Process"), "mode": "draft"},
            "variables": self.variables,
            "nodes": self.nodes,
            "edges": self.edges,
            "external_variables": [],
            "metadata": {"plan_rows": len(self.row_map)},
        }
        try:
            irlib.ProcessIR.parse(data)
        except irlib.IRError as exc:
            self._err("PLAN-013", "IR", f"IR не прошёл контракт: {exc}")
        return data

    def graph_summary(self) -> str:
        lines = [f"шагов в плане: {len(self.row_map)}  узлов: {len(self.nodes)}  связей: {len(self.edges)}", ""]
        for row in self.row_map:
            mark = "тех." if row["tech"] else "    "
            lines.append(f"  {mark} {row['id']:<14} {row['block']:<16} «{row['quote']}»")
        lines.append("")
        for e in self.edges:
            lines.append(f"  {e['from']:<14}:{e['from_port']:<10} → {e['to']:<14}:{e['to_port']}")
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Таблица шагов (plan.json) → IR со строгой проверкой")
    ap.add_argument("plan", type=Path)
    ap.add_argument("out", type=Path, nargs="?")
    ap.add_argument("--check", action="store_true", help="только проверить план")
    ap.add_argument("--profile", choices=["draft", "release", "strict"], default="release",
                    help="профиль проверки для --check (по умолчанию release)")
    ap.add_argument("--print-graph", action="store_true")
    args = ap.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))  # BOM-устойчиво: файлы правят в Windows
    try:
        comp = PlanCompiler(plan)
        data = comp.compile()
    except PlanError as exc:
        print(f"FAIL: {exc}")
        return 2
    ir_data = {"process": data["process"], "variables": data["variables"],
               "nodes": data["nodes"], "edges": data["edges"],
               "external_variables": [], "metadata": data["metadata"]}
    if args.print_graph:
        print(comp.graph_summary())
    if args.check:
        report = validator.validate_ir(irlib.ProcessIR.parse(data), args.profile,
                                       templates=comp.templates)
        if report["status"] != "passed":
            for issue in report["issues"]:
                if issue["severity"] in ("FATAL", "ERROR"):
                    print(f"FAIL: [{issue['code']}] {issue['location']}: {issue['message']}")
            return 2
        print(f"OK: план валиден ({args.profile}) — {len(comp.row_map)} шагов, "
              f"{len(comp.nodes)} узлов, {len(comp.edges)} связей")
        return 0
    if args.out is None:
        print("FAIL: не указан выходной файл (или используйте --check)")
        return 2
    args.out.write_text(json.dumps(ir_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OK: {args.out} — {len(comp.row_map)} шагов, {len(comp.nodes)} узлов, {len(comp.edges)} связей")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
