"""Валидатор (фаза 5): Python-слой — «всё умное» (PLAN.md D3).

Два входа:
- validate_ir(ir, profile, templates=None) — проверка IR до генерации (SEM-*, SHP-граф);
  templates (assets/block_templates.json) включает сверку properties/шаблонов,
  чтобы validate-ir не давал ложный зелёный перед build;
- validate_process_bytes(raw)  — пост-проверка готового .process: связи, ID, порты,
  Content, переменные, контейнеры (последний рубеж build).

Отчёт — машинный: [{code, severity, location, message}], status = passed|failed.
Коды семейств: references/validation-profiles.yaml; каждый assert валиден
и для valid-, и для invalid-фикстуры (tests/unit/test_validator.py).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from . import profiles
from .compiler import overrides_typed_field
from .ir import BLOCK_NAMES, NODE_TYPES, VARIABLE_TYPES, ProcessIR, resolve_physical
from .types import collections, data_table, primitives, text

# Hashtable*-блоки: main.md §12.2 confirmed, но golden-образца нет (tests GOLDEN_MISSING).
# При переданных templates неиспользуемость вычисляется по ним, это — fallback.
UNCONFIRMED_PHYSICAL = frozenset(
    {NODE_TYPES[k] for k in ("CollectionClear", "CollectionSetItem", "CollectionRemoveItem",
                             "CollectionContainsKey", "CollectionContainsValue")}
)

# Обязательные выходные порты (block-catalog: max_out=1 у каждого; семантика ветвления).
# Ключи — физические InternalName: алиас и физическое имя не должны расходиться.
REQUIRED_OUT_PORTS: dict[str, tuple[str, ...]] = {
    "IfCondition": ("Yes", "No"),
    "While": ("Iteration", "Next"),
    "For": ("Index", "Next"),
    "ForeachRows": ("Row", "Next"),
    "ForeachList": ("Item", "Next"),
    "TryCatchContainer": ("Next", "Out", "Error"),
    "CollapseContainer": ("Next", "Out"),
    "ProcessContainer": ("Next", "Error"),
}

# strict: raw-свойства блоков без типизированных полей — грамматика ПО @Type шаблона:
#   Enum  → один из EnumVariants (Seconds/Milliseconds, Ascending/Descending, …)
#   Bool  → "" | True | False
#   Expression/Variable/прочие → простой операнд ($Variable, $Index, число, $True/$False,
#   PS-строковый литерал); произвольный код запрещён.
_RAW_OPERAND = re.compile(
    r"""^(?:
        \$[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*
      | \$True | \$False
      | -?\d+(?:\.\d+)?
      | "[^"]*" | '[^']*'
    )$""",
    re.VERBOSE,
)

# Ссылки на переменные внутри raw-свойств произвольных блоков ("$Table1", "$Row").
_PROPERTY_VAR = re.compile(r"\$([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)")

# --- правила генерации, которые дёшево проверяются машинно (SKILL.md, правила 3/4/7).
# Все они — WARN: эвристика не должна блокировать легальные случаи (URL из задачи,
# длинное действие в ToDo), но обязана их подсвечивать.

# Правило 7: значения, похожие на выдуманную инфраструктуру.
INVENTED_INFRA = re.compile(
    r"(?:https?://"
    r"|\\\\[A-Za-z0-9._-]+\\"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:local|ru|com|org|net|io)\b)",
    re.UNICODE,
)

# Правило 7 (обратная сторона): значения, похожие на настоящий секрет.
SECRET_VALUE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|\bgsk_[A-Za-z0-9]{20,}"
    r"|\bsk-[A-Za-z0-9]{20,}"
    r"|\b[0-9a-fA-F]{32,}\b"
    r"|[?&]token=)",
)
_SECRET_CLASS = re.compile(r"^(?:Bearer|gsk_|sk-|[0-9a-fA-F]{32,}|[?&]token=)")
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+@")


def _secret_class(value: str) -> str:
    """Название класса секрета без самого значения (в отчёт значение не печатаем)."""
    match = SECRET_VALUE.search(value)
    token = match.group(0) if match else ""
    if token.startswith("Bearer"):
        return "Bearer-токен"
    if token.startswith("gsk_"):
        return "ключ Groq"
    if token.startswith("sk-"):
        return "ключ OpenAI"
    if token.startswith("token=") or token.startswith("&token="):
        return "токен в URL"
    return "hex-токен"


def _mask_credentials(text: str) -> str:
    """URL с логином/паролем → `://***@`, чтобы секрет не попадал в отчёт."""
    return _URL_CREDENTIALS.sub(r"\1***@", text)

# Правило 3: unresolved_action — имя действия, а не реализация.
TODO_IMPL_HINT = re.compile(r"(?i)(powershell|скрипт|регуляр|regex|выражени|invoke|;|\n)")
TODO_MAX_LEN = 60


def _property_specs(template: dict) -> dict[str, tuple[str, frozenset[str]]]:
    """@Type и EnumVariants свойств шаблона: {имя: (Type, варианты)}.

    "{x:Null}" — сериализационный sentinel Designer'а (встречается и пустая строка,
    167 против 142 в 50 эталонах), вариантов он не задаёт.
    """
    specs: dict[str, tuple[str, frozenset[str]]] = {}
    for p in ET.fromstring(template["content"]).iter():
        if p.tag.split("}")[-1] != "Property":
            continue
        variants = frozenset(v for v in (p.get("EnumVariants") or "").split(";") if v and v != "{x:Null}")
        specs[p.get("Name", "")] = (p.get("Type", ""), variants)
    return specs


def _template_ports(template: dict) -> dict[str, tuple[int, int]]:
    """{имя порта: (max_in, max_out)} из шаблона блока (каталог продукта)."""
    ports: dict[str, tuple[int, int]] = {}
    for c in ET.fromstring(template["content"]).iter():
        if c.tag.split("}")[-1] != "ConnectorSettings":
            continue
        try:
            ports[c.get("Name", "")] = (int(c.get("MaxInConnections") or 0), int(c.get("MaxOutConnections") or 0))
        except ValueError:
            ports[c.get("Name", "")] = (0, 0)
    return ports


def _port_max(value: object) -> int:
    """MaxIn/MaxOut из Content → int; битое значение считаем 0."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _raw_property_ok(spec: tuple[str, frozenset[str]] | None, raw: str) -> bool:
    """Допустимо ли значение raw-свойства в strict (спек — из шаблона, None — fallback)."""
    if spec is None:
        return bool(_RAW_OPERAND.match(raw))
    ptype, variants = spec
    if ptype == "Enum":
        return raw in variants
    if ptype == "Bool":
        return raw in ("", "True", "False")
    if ptype == "EnumMulti":
        parts = [p for p in raw.split(";") if p]
        return all(p in variants for p in parts)
    if ptype == "Double":
        return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", raw))
    if ptype in ("String", "Selector", "Image"):
        # String — сырое значение; Selector/Image — структурированные значения Designer'а,
        # они не являются PS-выражениями и грамматикой операндов не проверяются.
        return True
    return bool(_RAW_OPERAND.match(raw))

CONDITION_KINDS_LOGICAL = frozenset({"compare", "logical"})
EXPR_RESULT_TYPES = {
    "number_literal": "Number",
    "boolean_literal": "Logical",
    "string_literal": "Text",
    "list_literal": "List",
    "hashtable_literal": "Hashtable",
    "data_table_literal": "DataTable",
    "secure_encode": "SecureString",
    "secure_decode": "Text",
    "compare": "Logical",
    "logical": "Logical",
}


@dataclass
class Issue:
    code: str
    severity: str  # FATAL | ERROR | WARN
    location: str
    message: str
    rule: str  # словарное имя для профиля (unresolved_todo, …)


def _walk_expr(expr, out: list):
    out.append(expr)
    for side in ("left", "right"):
        sub = expr.get(side)
        if isinstance(sub, dict):
            _walk_expr(sub, out)


def _expr_variables(node: dict, field: str) -> list[str]:
    expr = node.get(field)
    if not isinstance(expr, dict):
        return []
    flat: list[dict] = []
    _walk_expr(expr, flat)
    return [e["variable"] for e in flat if e.get("kind") == "variable_reference" and e.get("variable")]


def _expr_static_type(expr: dict) -> str | None:
    kind = expr.get("kind")
    if kind in EXPR_RESULT_TYPES:
        return EXPR_RESULT_TYPES[kind]
    return None  # powershell / arithmetic / variable_reference — контекстно


def _while_body_keys(node: dict, edges: list) -> set[str]:
    """Узлы тела цикла: от Iteration-ребра до возврата в while.Input (упрощённо — по цепочке Next)."""
    entry = next((e["to"] for e in edges if e["from"] == node["key"] and e["from_port"] == "Iteration"), None)
    body: set[str] = set()
    cur = entry
    while cur and cur not in body:
        body.add(cur)
        cur = next((e["to"] for e in edges if e["from"] == cur and e["from_port"] == "Next" and e["to"] != node["key"]), None)
    return body


def validate_ir(parsed: ProcessIR, profile: str = "draft", templates: dict | None = None) -> dict:
    """Полная IR-валидация под профиль; возвращает {status, profile, issues}.

    templates — содержимое assets/block_templates.json (передаёт cli): включает
    SHP-PROP-002 (свойство вне шаблона) и точную SHP-BLK-010 (нет шаблона блока).
    """
    issues: list[Issue] = []
    nodes = parsed["nodes"]
    edges = parsed["edges"]
    variables = {v["name"]: v for v in parsed["variables"]}
    externals = {e.get("name") for e in parsed["external_variables"]}
    forbidden = profiles.blocking_codes(profile)
    node_by_key = {n["key"]: n for n in nodes}

    def emit(rule: str, code: str, severity: str, location: str, message: str) -> None:
        # forbidden-семантика: коды-«нарушители» эмитятся как ERROR всегда,
        # но блокируют только если правило запрещено профилем (иначе WARN).
        if rule in forbidden:
            issues.append(Issue(code, "ERROR", location, message, rule))
        elif rule in profiles.WARN_ONLY_IN_DRAFT:
            issues.append(Issue(code, "WARN", location, message, rule))
        else:
            issues.append(Issue(code, severity, location, message, rule))

    # --- SHP-GRF: граф ---
    keys = {n["key"] for n in nodes}
    # декоративные блоки (Comment и подобные) не имеют портов и в потоке не участвуют
    decorative: set[str] = set()
    if templates is not None:
        for n in nodes:
            physical_n = resolve_physical(n["type"])
            if physical_n in templates and not _template_ports(templates[physical_n]):
                decorative.add(n["key"])
    start = next((n for n in nodes if resolve_physical(n["type"]) == "Start"), None)
    if start:
        reachable: set[str] = set()
        frontier = [start["key"]]
        while frontier:
            cur = frontier.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            frontier.extend(e["to"] for e in edges if e["from"] == cur)
        for n in nodes:
            if n["key"] not in reachable and n["key"] not in decorative:
                issues.append(Issue("SHP-GRF-010", "ERROR", f"node {n['key']}", "блок недостижим от Start", "unreachable"))
    stops = {n["key"] for n in nodes if resolve_physical(n["type"]) == "ReturnOrStop"}
    if stops:
        # обратная достижимость до Stop
        to_stop: set[str] = set(stops)
        frontier = list(stops)
        while frontier:
            cur = frontier.pop()
            for e in edges:
                if e["to"] == cur and e["from"] not in to_stop:
                    to_stop.add(e["from"])
                    frontier.append(e["from"])
        for n in nodes:
            physical = resolve_physical(n["type"])
            if (n["key"] not in to_stop and n["key"] not in decorative
                    and physical not in ("ReturnOrStop", "FullAbort", "AbortContainer")):
                issues.append(Issue("SHP-GRF-011", "ERROR", f"node {n['key']}", "нет пути к Stop/терминальному блоку", "no_path_to_stop"))
    seen_edges = set()
    for e in edges:
        sig = (e["from"], e["from_port"], e["to"], e["to_port"])
        if sig in seen_edges:
            issues.append(Issue("SHP-GRF-012", "ERROR", f"edge {sig}", "дублирующая связь", "duplicate_edge"))
        seen_edges.add(sig)

    # --- SHP-PORT: порты и их арность — из каталога блока (MaxIn/MaxOut шаблона) ---
    if templates is not None:
        fan_out: dict[tuple[str, str], int] = {}
        fan_in: dict[tuple[str, str], int] = {}
        for e in edges:
            fan_out[(e["from"], e["from_port"])] = fan_out.get((e["from"], e["from_port"]), 0) + 1
            fan_in[(e["to"], e["to_port"])] = fan_in.get((e["to"], e["to_port"]), 0) + 1
        for e in edges:
            src = node_by_key[e["from"]]
            physical = resolve_physical(src["type"])
            if physical in templates:
                ports = _template_ports(templates[physical])
                if e["from_port"] not in ports:
                    issues.append(Issue("SHP-PORT-001", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"у блока {physical} нет выходного порта {e['from_port']!r} "
                                        f"(каталог: {sorted(ports)})",
                                        "unknown_port"))
                elif ports[e["from_port"]][1] == 0:
                    issues.append(Issue("SHP-PORT-001", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"порт {e['from_port']} блока {physical} не является выходом "
                                        f"(MaxOutConnections=0)",
                                        "unknown_port"))
                elif fan_out[(e["from"], e["from_port"])] > ports[e["from_port"]][1]:
                    issues.append(Issue("SHP-PORT-002", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"порт {e['from_port']} блока {physical} допускает "
                                        f"{ports[e['from_port']][1]} исходящих связей, а используется "
                                        f"{fan_out[(e['from'], e['from_port'])]}",
                                        "port_fan_out"))
            dst = node_by_key[e["to"]]
            physical_dst = resolve_physical(dst["type"])
            if physical_dst in templates:
                ports_dst = _template_ports(templates[physical_dst])
                if e["to_port"] not in ports_dst:
                    issues.append(Issue("SHP-PORT-001", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"у блока {physical_dst} нет входного порта {e['to_port']!r} "
                                        f"(каталог: {sorted(ports_dst)})",
                                        "unknown_port"))
                elif ports_dst[e["to_port"]][0] == 0:
                    issues.append(Issue("SHP-PORT-001", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"порт {e['to_port']} блока {physical_dst} не является входом "
                                        f"(MaxInConnections=0)",
                                        "unknown_port"))
                elif fan_in[(e["to"], e["to_port"])] > ports_dst[e["to_port"]][0]:
                    issues.append(Issue("SHP-PORT-002", "ERROR", f"edge {e['from']}→{e['to']}",
                                        f"вход {e['to_port']} блока {physical_dst} допускает "
                                        f"{ports_dst[e['to_port']][0]} связей, а используется "
                                        f"{fan_in[(e['to'], e['to_port'])]}",
                                        "port_fan_in"))

    # --- SHP-GRF-020/021: обязательные выходы и границы циклов ---
    for n in nodes:
        physical = resolve_physical(n["type"])
        required = REQUIRED_OUT_PORTS.get(physical)
        if required:
            used_out = {e["from_port"] for e in edges if e["from"] == n["key"]}
            for port in required:
                if port not in used_out:
                    emit("unconnected_branch", "SHP-GRF-020", "WARN", f"node {n['key']}",
                         f"неподключённый обязательный выход {port} ({n['type']})")
        if physical == "For":
            if n.get("end_index") is None:
                emit("for_bounds", "SHP-GRF-021", "WARN", f"node {n['key']}",
                     "For без end_index: граница повторов не задана")
            step = n.get("step")
            if (isinstance(step, dict) and step.get("kind") == "number_literal"
                    and step.get("value") == 0):
                emit("for_bounds", "SHP-GRF-021", "WARN", f"node {n['key']}",
                     "For со step=0: бесконечный цикл")

    # --- SHP-TODO / SHP-BLK ---
    for n in nodes:
        physical = resolve_physical(n["type"])
        if physical == "ToDoBlock":
            emit("unresolved_todo", "SHP-TODO-001", "WARN" if profile == "draft" else "ERROR",
                 f"node {n['key']}", f"ToDo-плейсхолдер: {n.get('unresolved_action')}")
        if templates is not None:
            missing_template = physical is None or physical not in templates
        else:
            # без шаблонов достоверно знаем только исторический список
            # неподтверждённых Hashtable-блоков; остальное проверит build.
            missing_template = physical in UNCONFIRMED_PHYSICAL
        if missing_template:
            # без golden-шаблона блок не компилируется ни в одном профиле — не «строгость», а факт
            issues.append(Issue("SHP-BLK-010", "ERROR", f"node {n['key']}",
                                f"блок {n['type']} не подтверждён golden (нет шаблона) — build невозможен",
                                "unconfirmed_collection_block"))

    # --- SEM-REF: переменные в выражениях ---
    declared = set(variables)
    for n in nodes:
        physical = resolve_physical(n["type"])
        refs = _expr_variables(n, "condition")
        refs += _expr_variables(n, "expression")
        if physical == "For" and n.get("index_variable"):
            refs.append(n["index_variable"])
        for a in n.get("assignments", []):
            refs += _expr_variables(a, "expression")
        for r in refs:
            if r not in declared and r not in externals:
                emit("variable_undeclared", "SEM-REF-001", "WARN", f"node {n['key']}",
                     f"переменная {r!r} не объявлена и не внешняя")

    # --- SEM-EXPR / SEM-TYPE ---
    for n in nodes:
        physical = resolve_physical(n["type"])
        if physical in ("IfCondition", "While"):
            c = n["condition"]
            ck = c.get("kind")
            if profile == "strict" and ck not in CONDITION_KINDS_LOGICAL:
                if not (ck == "variable_reference" and variables.get(c.get("variable"), {}).get("sherpa_type") == "Logical"):
                    emit("untyped_expression_in_condition", "SEM-EXPR-011", "ERROR", f"node {n['key']}",
                         "условие должно быть compare/logical/ссылкой на Logical")
            if ck == "variable_reference":
                vt = variables.get(c.get("variable"), {}).get("sherpa_type")
                if vt not in (None, "Logical"):
                    emit("implicit_truthiness", "SEM-EXPR-010", "WARN", f"node {n['key']}",
                         f"неявная истинность: {c.get('variable')} имеет тип {vt}, а не Logical")
        if physical == "Assign":
            for i, a in enumerate(n.get("assignments", []), start=1):
                target = variables.get(a["target"])
                if target:
                    want = target["sherpa_type"]
                    got = _expr_static_type(a["expression"])
                    if got and got != want:
                        sev = "ERROR" if "ambiguous_type_coercion" in forbidden else "WARN"
                        issues.append(Issue("SEM-TYPE-001", sev, f"node {n['key']}.Expression{i}",
                                            f"тип выражения {got} не совпадает с типом {a['target']} ({want})",
                                            "ambiguous_type_coercion"))
        if physical == "For" and n.get("index_variable"):
            vt = variables.get(n["index_variable"], {}).get("sherpa_type")
            if vt not in (None, "Number"):
                emit("forloop_index_type", "SEM-TYPE-002", "WARN", f"node {n['key']}",
                     f"переменная цикла {n['index_variable']} имеет тип {vt}, а For ожидает Number")

    # --- SHP-PROP: raw-свойства против типизированных полей, шаблона и грамматики ---
    specs_cache: dict[str, dict[str, tuple[str, frozenset[str]]]] = {}
    for n in nodes:
        physical = resolve_physical(n["type"])
        tpl_defaults: dict[str, str] | None = None
        if templates is not None and physical in templates:
            tpl_defaults = templates[physical].get("property_defaults", {})
            if physical not in specs_cache:
                specs_cache[physical] = _property_specs(templates[physical])
        specs = specs_cache.get(physical, {})
        for name, raw in (n.get("properties") or {}).items():
            if not isinstance(raw, str):
                issues.append(Issue("SHP-PROP-003", "ERROR", f"node {n['key']}.{name}",
                                    f"значение properties.{name!r} должно быть PS-строкой (не {type(raw).__name__})",
                                    "bad_property_value"))
                continue
            if overrides_typed_field(n["type"], name):
                issues.append(Issue("SHP-PROP-001", "ERROR", f"node {n['key']}.{name}",
                                    f"raw-свойство {name!r} перекрывает типизированное поле — "
                                    f"задайте его через контракт IR, а не properties",
                                    "typed_field_override"))
                continue
            if tpl_defaults is not None and name not in specs:
                issues.append(Issue("SHP-PROP-002", "ERROR", f"node {n['key']}.{name}",
                                    f"свойство {name!r} отсутствует в golden-шаблоне блока {physical}",
                                    "unknown_property"))
                continue
            if not _raw_property_ok(specs.get(name), raw):
                emit("raw_property_grammar", "SEM-PROP-002", "WARN", f"node {n['key']}.{name}",
                     "значение не проходит грамматику свойства (@Type шаблона: Enum/Bool/операнд) — в strict запрещено")
            # Правило 4: свойство, заданное пустым или дефолтом, ничего не меняет —
            # компилятор и так берёт значение шаблона. Такое свойство только шумит в диффе.
            if tpl_defaults is not None and name in specs:
                default = tpl_defaults.get(name, "")
                if raw == "" or raw == default:
                    emit("redundant_property", "SHP-GEN-001", "WARN", f"node {n['key']}.{name}",
                         "свойство задано пустым/дефолтным значением — правило 4: чего задача "
                         "не задала, остаётся дефолтом шаблона (уберите свойство)")

    # --- SHP-GEN-002/003/004: правила 3 и 7 (ToDo = действие; не выдумывать значения) ---
    def _value_heuristics(location: str, value: str) -> None:
        if not value:
            return
        if SECRET_VALUE.search(value):
            # значение в отчёт не печатаем — только класс секрета
            emit("secret_in_ir", "SHP-GEN-004", "WARN", location,
                 f"значение похоже на секрет ({_secret_class(value)}) — секрет не должен "
                 f"попадать в .process: вынесите в учётные данные/параметр робота")
            return
        infra = INVENTED_INFRA.search(value)
        if infra:
            emit("invented_infrastructure", "SHP-GEN-003", "WARN", location,
                 f"значение похоже на выдуманную инфраструктуру "
                 f"({_mask_credentials(infra.group(0)[:40])}) — правило 7: если задача его "
                 f"не давала, оставьте пустую заготовку")

    for v in parsed["variables"]:
        initial = v.get("initial") or {}
        if isinstance(initial, dict) and initial.get("kind") == "string_literal":
            _value_heuristics(f"variable {v['name']}.initial", str(initial.get("value") or ""))
    for n in nodes:
        for name, raw in (n.get("properties") or {}).items():
            if isinstance(raw, str):
                _value_heuristics(f"node {n['key']}.{name}", raw)
        if resolve_physical(n["type"]) == "ToDoBlock":
            action = (n.get("unresolved_action") or "").strip()
            hint = TODO_IMPL_HINT.search(action)
            if len(action) > TODO_MAX_LEN or hint:
                marker = f", маркер {hint.group(0)!r}" if hint else ""
                emit("todo_action_quality", "SHP-GEN-002", "WARN", f"node {n['key']}",
                     f"unresolved_action — имя действия, а не реализация ({len(action)} символов"
                     f"{marker}): {action[:60]!r}")

    # --- SHP-IR-001: мёртвые поля (компилятор игнорирует, граф задаётся рёбрами) ---
    for n in nodes:
        for dead in ("branches", "body", "next"):
            if n.get(dead) not in (None, {}, []):
                issues.append(Issue("SHP-IR-001", "WARN", f"node {n['key']}",
                                    f"поле {dead!r} игнорируется компилятором — граф задаётся рёбрами (edges)",
                                    "dead_ir_field"))

    # --- SEM-VAR-010: Dictionary не генерируется (контейнер не подтверждён golden) ---
    for v in parsed["variables"]:
        if v["sherpa_type"] == "Dictionary":
            issues.append(Issue("SEM-VAR-010", "ERROR", f"variable {v['name']}",
                                "Dictionary-переменные не генерируются (контейнер не подтверждён golden) — "
                                "нужен образец из Designer; временно используйте Hashtable",
                                "dictionary_variable"))

    # --- SEM-VAR-011: непустой SecureString в декларации ---
    # Компилятор такой IR не соберёт (CompileError), поэтому ошибка видна до сборки:
    # секреты в XML не пишутся, значение задаётся через _tosecure в Assign.
    for v in parsed["variables"]:
        if v["sherpa_type"] != "SecureString":
            continue
        initial = v.get("initial")
        if initial is None:
            continue
        empty = (initial.get("kind") == "string_literal" and initial.get("value") == "")
        if not empty:
            issues.append(Issue("SEM-VAR-011", "ERROR", f"variable {v['name']}",
                                "SecureString в декларации объявляется только пустым: непустое "
                                "начальное значение компилятор не собирает (секрет — через "
                                "Assign + _tosecure)",
                                "nonempty_secure_string"))

    # --- SEM-EXPR-012/013: freeform PowerShell / InvokeExpression ---
    # draft|release — WARN (информационно), strict — ERROR (forbidden-механизм профилей).
    # powershell запрещён на ЛЮБОЙ глубине AST, не только в корне.
    def _ps_warn(expr: object, loc: str) -> None:
        if not isinstance(expr, dict) or "kind" not in expr:
            return
        flat: list[dict] = []
        _walk_expr(expr, flat)
        for e in flat:
            if e.get("kind") == "powershell":
                emit("freeform_powershell", "SEM-EXPR-012", "WARN", loc,
                     "kind=powershell в поддереве выражения — в strict запрещён")

    for v in parsed["variables"]:
        _ps_warn(v.get("initial"), f"variable {v['name']}.initial")
    for n in nodes:
        _ps_warn(n.get("condition"), f"node {n['key']}.condition")
        _ps_warn(n.get("expression"), f"node {n['key']}.expression")
        for f in ("start_index", "end_index", "step"):
            _ps_warn(n.get(f), f"node {n['key']}.{f}")
        for i, a in enumerate(n.get("assignments", []), start=1):
            _ps_warn(a.get("expression"), f"node {n['key']}.Expression{i}")
        if resolve_physical(n["type"]) == "InvokeExpression":
            emit("invoke_expression", "SEM-EXPR-013", "WARN", f"node {n['key']}",
                 "InvokeExpression — блок произвольного PowerShell; в strict запрещён")

    # --- SEM-LOOP: эвристики While (§17.7) ---
    for n in nodes:
        if resolve_physical(n["type"]) != "While":
            continue
        body = _while_body_keys(n, edges)
        if not body:
            issues.append(Issue("SEM-LOOP-001", "WARN", f"node {n['key']}", "пустое тело цикла", "empty_body"))
        useful = {"Assign", "InvokeExpression"} | {k for k in NODE_TYPES if k.startswith(("List", "Table", "Collection"))}
        if body and not any(node_by_key[k]["type"] in useful for k in body):
            issues.append(Issue("SEM-LOOP-002", "WARN", f"node {n['key']}",
                                "тело цикла не содержит блоков, меняющих состояние", "body_no_state_change"))
        cond = n["condition"]
        if cond.get("kind") == "compare":
            lt, rt = cond.get("left", {}), cond.get("right", {})
            if lt.get("kind") == "number_literal" and rt.get("kind") == "number_literal":
                ops = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "lt": lambda a, b: a < b,
                       "le": lambda a, b: a <= b, "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b}
                op = ops.get(cond.get("operator"))
                if op and op(lt["value"], rt["value"]):
                    issues.append(Issue("SEM-LOOP-003", "WARN", f"node {n['key']}",
                                        "условие статически всегда истинно — бесконечный цикл", "always_true"))

    # --- SHP-VAR-008: неиспользуемые переменные ---
    used: set[str] = set()
    for n in nodes:
        used.update(_expr_variables(n, "condition"))
        used.update(_expr_variables(n, "expression"))
        for a in n.get("assignments", []):
            used.add(a["target"])
            used.update(_expr_variables(a, "expression"))
        for f in ("start_index", "end_index", "step"):
            used.update(_expr_variables(n, f))
        # raw-свойства произвольных блоков ссылаются на переменные как "$Имя"
        for raw in (n.get("properties") or {}).values():
            if isinstance(raw, str):
                used.update(_PROPERTY_VAR.findall(raw))
    # индекс For механически потребляется самим циклом: фиксированное число
    # повторов без чтения $Index — законная конструкция, шума в отчёте быть не должно
    for n in nodes:
        if resolve_physical(n["type"]) == "For" and n.get("index_variable"):
            used.add(n["index_variable"])
    for v in parsed["variables"]:
        if v["name"] not in used:
            issues.append(Issue("SHP-VAR-008", "WARN", f"variable {v['name']}", "переменная не используется", "unused_variable"))

    blocking = [i for i in issues if i.severity in profiles.BLOCKING_SEVERITIES]
    return {
        "status": "failed" if blocking else "passed",
        "profile": profile,
        "issues": [asdict(i) for i in issues],
    }


# --- .process-слой (готовый файл: golden или сгенерированный) ---


def validate_process(path: Path | str) -> dict:
    """Пост-проверка готового файла; читает байты и делегирует validate_process_bytes."""
    return validate_process_bytes(Path(path).read_bytes())


def validate_process_bytes(raw: bytes) -> dict:
    """Пост-проверка сгенерированного/чужого .process по байтам (последний рубеж build).

    Покрывает: well-formed + UTF-8, уникальность ID, парсинг Content каждого блока,
    связи (ID + имена коннекторов против портов шаблона блока), DiagramProperty
    (тип, DefaultValue/Value, контейнеры, пустой SecureString).
    """
    issues: list[Issue] = []
    if not raw.startswith(b"\xef\xbb\xbf"):
        issues.append(Issue("SHP-DOC-000", "WARN", "file", "нет UTF-8 BOM (формат golden — с BOM)", "no_bom"))
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return {"status": "failed", "profile": "process",
                "issues": [asdict(Issue("SHP-DOC-001", "FATAL", "file", f"файл не является UTF-8: {exc}", "bad_encoding"))]}
    try:
        root = ET.fromstring(decoded)
    except ET.ParseError as exc:
        return {"status": "failed", "profile": "process",
                "issues": [asdict(Issue("SHP-DOC-001", "FATAL", "file", f"XML не парсится: {exc}", "not_well_formed"))]}

    # --- блоки: уникальность ID, парсинг Content, InternalName/Version, порты ---
    seen_ids: set[str] = set()
    ports_by_id: dict[str, dict[str, tuple[str, str]]] = {}
    for item in root.findall(".//DesignerItem"):
        iid = item.findtext("ID") or ""
        if iid in seen_ids:
            issues.append(Issue("SHP-ID-006", "ERROR", f"block {iid}", "дублирующийся ID блока", "duplicate_block_id"))
        seen_ids.add(iid)
        content = item.findtext("Content") or ""
        try:
            inner = ET.fromstring(content)
        except ET.ParseError as exc:
            issues.append(Issue("SHP-DOC-002", "ERROR", f"block {iid}", f"Content не парсится: {exc}", "bad_content"))
            continue
        iname = inner.get("InternalName") or ""
        if not iname:
            issues.append(Issue("SHP-DOC-003", "ERROR", f"block {iid}",
                                "у блока нет InternalName в Content", "no_internal_name"))
        elif iname not in BLOCK_NAMES:
            issues.append(Issue("SHP-DOC-005", "ERROR", f"block {iid}",
                                f"InternalName {iname!r} отсутствует в каталоге блоков", "unknown_block"))
        elif not (inner.get("Version") or "").isdigit():
            issues.append(Issue("SHP-DOC-004", "ERROR", f"block {iid}",
                                f"некорректная версия блока {iname}: {inner.get('Version')!r}", "bad_block_version"))
        ports_by_id[iid] = {}
        for c in inner.iter():
            if c.tag.split("}")[-1] == "ConnectorSettings":
                ports_by_id[iid][c.get("Name", "")] = (
                    c.get("MaxInConnections", "0"), c.get("MaxOutConnections", "0")
                )

    # --- связи: ID, имена коннекторов и арность против портов блоков ---
    fan_out: dict[tuple[str, str], int] = {}
    fan_in: dict[tuple[str, str], int] = {}
    for c in root.findall("./Connections/Connection"):
        src, sink = c.findtext("SourceID") or "", c.findtext("SinkID") or ""
        if src not in seen_ids:
            issues.append(Issue("SHP-ID-004", "ERROR", "Connections", "SourceID ссылается на несуществующий блок", "dangling_source"))
        if sink not in seen_ids:
            issues.append(Issue("SHP-ID-005", "ERROR", "Connections", "SinkID ссылается на несуществующий блок", "dangling_sink"))
        sport, kport = c.findtext("SourceConnectorName") or "", c.findtext("SinkConnectorName") or ""
        src_port = ports_by_id.get(src, {}).get(sport)
        if src in ports_by_id and (src_port is None or _port_max(src_port[1]) <= 0):
            issues.append(Issue("SHP-ID-007", "ERROR", "Connections",
                                f"SourceConnectorName {sport!r} не является выходом (max_out>0) блока-источника",
                                "bogus_source_port"))
        elif src in ports_by_id:
            fan_out[(src, sport)] = fan_out.get((src, sport), 0) + 1
        sink_port = ports_by_id.get(sink, {}).get(kport)
        if sink in ports_by_id and (sink_port is None or _port_max(sink_port[0]) <= 0):
            issues.append(Issue("SHP-ID-008", "ERROR", "Connections",
                                f"SinkConnectorName {kport!r} не является входом (max_in>0) блока-приёмника",
                                "bogus_sink_port"))
        elif sink in ports_by_id:
            fan_in[(sink, kport)] = fan_in.get((sink, kport), 0) + 1

    for (bid, port), count in fan_out.items():
        limit = _port_max(ports_by_id[bid][port][1])
        if limit > 0 and count > limit:
            issues.append(Issue("SHP-PORT-002", "ERROR", "Connections",
                                f"порт {port} блока {bid[:8]} допускает {limit} исходящих связей, а используется {count}",
                                "port_fan_out"))
    for (bid, port), count in fan_in.items():
        limit = _port_max(ports_by_id[bid][port][0])
        if limit > 0 and count > limit:
            issues.append(Issue("SHP-PORT-002", "ERROR", "Connections",
                                f"вход {port} блока {bid[:8]} допускает {limit} связей, а используется {count}",
                                "port_fan_in"))

    # --- переменные: DefaultValue и Value проверяются наравне ---
    names = set()
    for p in root.findall(".//DiagramProperty"):
        name, vtype = p.get("Name"), p.get("Type")
        dv, val = p.get("DefaultValue"), p.get("Value")
        if name in names:
            issues.append(Issue("SHP-VAR-002", "ERROR", f"variable {name}", "дублирующееся имя переменной", "dup_var"))
        names.add(name)
        if vtype not in VARIABLE_TYPES:
            issues.append(Issue("SHP-VAR-007", "ERROR", f"variable {name}",
                                f"тип {vtype!r} не подтверждён (подтверждённые — type-catalog.yaml)",
                                "invented_variable_type"))
        if dv is None or val is None:
            issues.append(Issue("SHP-VAR-009", "ERROR", f"variable {name}",
                                "у переменной нет DefaultValue/Value", "missing_default_value"))
            continue
        for label, v in (("DefaultValue", dv), ("Value", val)):
            if vtype == "SecureString" and v != "":
                issues.append(Issue("SHP-VAR-010", "ERROR", f"variable {name}.{label}",
                                    "SecureString в декларации должен быть пустым (секреты не пишутся в XML)",
                                    "nonempty_securestring"))
            try:
                if vtype == "Text" and v:
                    text.decode(v)
                elif vtype == "List" and v:
                    collections.decode_list(v)
                elif vtype == "Hashtable" and v:
                    collections.decode_hashtable(v)
                elif vtype == "DataTable" and v:
                    data_table.decode(v)
                elif vtype == "Logical" and v not in ("True", "False"):
                    issues.append(Issue("SHP-VAR-006", "ERROR", f"variable {name}.{label}",
                                        "Logical хранит не True/False", "bad_logical"))
            except ValueError as exc:
                issues.append(Issue("SEM-SER-001", "ERROR", f"variable {name}.{label}",
                                    f"контейнер не читается: {exc}", "bad_container"))

    blocking = [i for i in issues if i.severity in profiles.BLOCKING_SEVERITIES]
    return {"status": "failed" if blocking else "passed", "profile": "process",
            "issues": [asdict(i) for i in issues]}
