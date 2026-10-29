"""Компилятор IR → .process (фаза 4).

Библиотека-over-invented: XML блоков не конструируется, а берётся канонический
шаблон блока из библиотеки установленного Designer (assets/block_templates.json,
scripts/dev/harvest_catalog.py); компилятор только подставляет Property/@Value,
идентификаторы, раскладку и собирает граф.

Формат выходного файла сверен с golden: UTF-8 BOM, CRLF, отступы 2/4/6,
порядок секций Root: DesignerItems, Connections, Properties, PSCache,
Description, InputArguments, OutputArguments.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import identifiers, layout
from .ir import ProcessIR, resolve_physical
from .types import collections, data_table, primitives, text

ZERO_GUID = "00000000-0000-0000-0000-000000000000"

COMPARE_PS = {"eq": "-eq", "ne": "-ne", "lt": "-lt", "le": "-le", "gt": "-gt", "ge": "-ge",
              "like": "-like", "match": "-match"}
LOGICAL_PS = {"and": "-and", "or": "-or", "xor": "-xor", "not": "-not"}
ARITHMETIC_PS = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}


class CompileError(ValueError):
    """Нарушение инварианта при генерации (порт, типизация, неподтверждённая ветка)."""


# Свойства, управляемые типизированными полями IR (AST), а не raw properties.
# Raw-перекрытие любого из них — ошибка SHP-PROP-001 (валидатор).
_TYPED_EXACT: dict[str, frozenset[str]] = {
    # Ключи — физические InternalName: алиасы и физические имена должны вести
    # себя одинаково (SKILL: тип узла — любой InternalName каталога).
    "IfCondition": frozenset({"Condition"}),
    "While": frozenset({"Condition"}),
    "For": frozenset({"StartIndex", "EndIndex", "Step", "Index"}),
    "InvokeExpression": frozenset({"Expression1"}),
}
_TYPED_ASSIGN_PROPS = re.compile(r"^(?:Variable|Expression)(?:[1-9]|10)$")


def overrides_typed_field(node_type: str, prop: str) -> bool:
    """True, если raw-свойство перекрывает поле, управляемое типизированным IR."""
    physical = resolve_physical(node_type) or node_type
    if physical == "Assign":
        return bool(_TYPED_ASSIGN_PROPS.match(prop))
    return prop in _TYPED_EXACT.get(physical, frozenset())


def xml_attr_escape(value: str) -> str:
    """Экранирование значения атрибута для inner-XML Content.

    Переводы строк обязаны стать сущностями `&#xA;` (иначе XML-нормализация
    атрибутов заменит их пробелами — проверено на DisplayName из InitAllSettings.process,
    где Designer пишет многострочный комментарий).
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
        .replace("\r", "&#xD;")
        .replace("\n", "&#xA;")
    )


def ps_expression(expr: dict, top: bool = True) -> str:
    """Выражение IR → PowerShell. Скобки: составные операнды, не верхний уровень.

    Подтверждённые формы golden: `$Переменная1 -eq $True` (b18),
    `Test-Connection … -Quiet` (init), `10+$a` (b17).
    """
    kind = expr["kind"]
    if kind == "variable_reference":
        return "$" + expr["variable"]
    if kind == "number_literal":
        return primitives.number(expr["value"])
    if kind == "boolean_literal":
        return primitives.logical_expression(expr["value"])
    if kind == "string_literal":
        return text.ps_literal(expr["value"])
    if kind == "powershell":
        return expr["value"]
    if kind == "secure_encode":
        return f"_tosecure({ps_expression(expr['left'], top=False)})"
    if kind == "secure_decode":
        return f"_fromsecure({ps_expression(expr['left'], top=False)})"
    if kind == "compare":
        left, right = _operand(expr, "left"), _operand(expr, "right")
        return f"{left} {COMPARE_PS[expr['operator']]} {right}" if top else f"({left} {COMPARE_PS[expr['operator']]} {right})"
    if kind == "logical":
        if expr["operator"] == "not":
            inner = _operand(expr, "left")
            return f"-not {inner}" if top else f"(-not {inner})"
        left, right = _operand(expr, "left"), _operand(expr, "right")
        op = LOGICAL_PS[expr["operator"]]
        return f"{left} {op} {right}" if top else f"({left} {op} {right})"
    if kind == "arithmetic":
        if expr["operator"] == "pow":
            raise CompileError("оператор pow не подтверждён golden (PowerShell не имеет **; [Math]::Pow вне контракта)")
        left, right = _operand(expr, "left"), _operand(expr, "right")
        op = ARITHMETIC_PS[expr["operator"]]
        return f"{left} {op} {right}" if top else f"({left} {op} {right})"
    if kind in ("list_literal", "hashtable_literal", "data_table_literal"):
        raise CompileError(f"{kind} допустим только как начальное значение переменной, не в выражении")
    raise CompileError(f"неизвестный kind выражения: {kind}")


def _operand(expr: dict, side: str) -> str:
    """Операнд бинарного узла: составные выражения — в скобках, атомы — как есть."""
    sub = expr[side]
    if sub["kind"] in ("variable_reference", "number_literal", "boolean_literal", "string_literal", "powershell"):
        return ps_expression(sub, top=True)
    return ps_expression(sub, top=False)


def initial_value(sherpa_type: str, initial: dict | None) -> str:
    """Начальное значение DiagramProperty по типу переменной (writer'ы фазы 3)."""
    if initial is None:
        if sherpa_type == "Number":
            return primitives.number(0)
        if sherpa_type == "Logical":
            return primitives.logical_declaration(False)
        if sherpa_type == "Text":
            return text.encode("")
        if sherpa_type == "SecureString":
            return primitives.secure_string_empty()
        if sherpa_type == "List":
            return collections.encode_list([])
        if sherpa_type == "Hashtable":
            return collections.encode_hashtable([])
        if sherpa_type == "DataTable":
            return data_table.encode(data_table.build_schema(None), data_table.build_diffgram(None))
        if sherpa_type in ("Object", "DateTime", "Money"):
            # каталог продукта объявляет эти типы, но контейнер начального значения не
            # подтверждён golden-образцом: объявляем пустым значением (как Designer для Object)
            return ""
        if sherpa_type == "Dictionary":
            raise CompileError("пустой Dictionary-контейнер не подтверждён golden — объявляйте без начального значения нельзя; запросите образец")
        raise CompileError(f"тип {sherpa_type} без initial не поддержан")
    kind = initial["kind"]
    if sherpa_type == "Number":
        if kind != "number_literal":
            raise CompileError("Number.initial должен быть number_literal")
        return primitives.number(initial["value"])
    if sherpa_type == "Logical":
        if kind != "boolean_literal":
            raise CompileError("Logical.initial должен быть boolean_literal")
        return primitives.logical_declaration(initial["value"])
    if sherpa_type == "Text":
        if kind != "string_literal":
            raise CompileError("Text.initial должен быть string_literal")
        return text.encode(initial["value"])
    if sherpa_type == "SecureString":
        if kind == "string_literal" and initial["value"] == "":
            return primitives.secure_string_empty()
        raise CompileError("непустой SecureString в декларации не подтверждён golden (§10.4)")
    if sherpa_type == "List":
        if kind != "list_literal":
            raise CompileError("List.initial должен быть list_literal")
        return collections.encode_list(initial["value"])
    if sherpa_type == "Hashtable":
        if kind != "hashtable_literal":
            raise CompileError("Hashtable.initial должен быть hashtable_literal")
        return collections.encode_hashtable([tuple(p) for p in initial["value"]])
    if sherpa_type == "DataTable":
        if kind != "data_table_literal":
            raise CompileError("DataTable.initial должен быть data_table_literal")
        spec = initial["value"]
        return data_table.encode(
            data_table.build_schema([tuple(c) for c in spec["columns"]]),
            data_table.build_diffgram(spec["rows"]),
        )
    if sherpa_type == "Dictionary":
        raise CompileError("начальное значение Dictionary не поддержано (encode не подтверждён golden)")
    if sherpa_type == "DateTime":
        if kind == "datetime_literal":
            return initial["value"]
        if kind == "string_literal" and initial["value"] == "":
            return ""
        raise CompileError("DateTime.initial должен быть datetime_literal (dd.MM.yyyy HH:mm:ss)")
    if sherpa_type in ("Object", "Money"):
        if kind == "string_literal" and initial["value"] == "":
            return ""
        raise CompileError(
            f"непустое начальное значение {sherpa_type} не подтверждено golden-образцом — "
            f"объявите переменную пустой и присвойте значение через Assign"
        )
    raise CompileError(f"тип {sherpa_type} не поддержан")


def node_property_values(node: dict) -> dict[str, str]:
    """Значения Property/@Value, задаваемые IR для узла (ключ — имя свойства)."""
    ntype = node["type"]
    physical = resolve_physical(ntype) or ntype
    values: dict[str, str] = {}
    if physical == "Assign":
        if len(node.get("assignments", [])) > 10:
            raise CompileError("Assign: максимум 10 пар (§15.2)")
        for i, pair in enumerate(node.get("assignments", []), start=1):
            values[f"Variable{i}"] = "$" + pair["target"]
            values[f"Expression{i}"] = ps_expression(pair["expression"])
    elif physical == "IfCondition":
        values["Condition"] = ps_expression(node["condition"])
    elif physical == "While":
        values["Condition"] = ps_expression(node["condition"])
    elif physical == "For":
        if node.get("end_index") is None:
            raise CompileError(f"{node['key']}: For требует end_index")
        step = node.get("step")
        if step is not None and step.get("kind") == "number_literal" and step.get("value") == 0:
            raise CompileError(f"{node['key']}: For не допускает step=0 (бесконечный цикл)")
        values["StartIndex"] = ps_expression(node["start_index"]) if node.get("start_index") is not None else "0"
        values["EndIndex"] = ps_expression(node["end_index"])
        values["Step"] = ps_expression(step) if step is not None else "1"
        if node.get("index_variable"):
            values["Index"] = "$" + node["index_variable"]
    elif physical == "InvokeExpression":
        # только типизированный путь (AST); raw-перекрытие Expression1 запрещено SHP-PROP-001
        if node.get("expression") is None:
            raise CompileError(f"{node['key']}: InvokeExpression требует 'expression'")
        values["Expression1"] = ps_expression(node["expression"])
    # прочие блоки (коллекции/таблицы/Delay/контейнеры): raw PS-строки из properties;
    # перекрытие типизированных полей отсекается валидатором (SHP-PROP-001)
    for name, raw in node.get("properties", {}).items():
        if not isinstance(raw, str):
            raise CompileError(f"{node['key']}.{name}: значение свойства должно быть PS-строкой")
        if overrides_typed_field(ntype, name):
            raise CompileError(
                f"{node['key']}.{name}: raw-свойство перекрывает типизированное поле — задайте через контракт IR"
            )
        values[name] = raw
    return values


def fill_template(
    template_content: str,
    values: dict[str, str],
    defaults: dict[str, str],
    display_name: str | None = None,
) -> str:
    """Подстановка Property/@Value и DisplayName в сырой XML шаблона.

    Правила (golden best_screenshots_mail_save):
    - DisplayName — смысловой: у ToDo это имя отсутствующего действия
      («Сделать снимок экрана»), у условия — текст условия;
    - свойства из `values` — наши; все остальные Value сбрасываются
      в DefaultValue шаблона (никаких пустых "" сверх заданного).
    """
    result = template_content
    if display_name:
        escaped = xml_attr_escape(display_name)
        result, n = re.subn(r'(DisplayName=")[^"]*(")', lambda m: m.group(1) + escaped + m.group(2), result, count=1)
        if n != 1:
            raise CompileError("DisplayName не найден в шаблоне")
    prop_names = set(defaults) | set(values)
    for name in sorted(prop_names):
        new_value = values.get(name, defaults.get(name, ""))
        escaped = xml_attr_escape(new_value)
        pattern = re.compile(r'(<Property[^>]*\bName="' + re.escape(name) + r'"[^>]*\bValue=")[^"]*(")')
        result, n = pattern.subn(lambda m: m.group(1) + escaped + m.group(2), result, count=1)
        if name in values and n != 1:
            raise CompileError(f"свойство {name!r} не найдено в шаблоне")
    return result


class Compiler:
    def __init__(self, templates_path: Path | str | None = None):
        if templates_path is None:
            templates_path = Path(__file__).resolve().parents[2] / "assets" / "block_templates.json"
        self.templates = json.loads(Path(templates_path).read_text(encoding="utf-8"))

    def compile(self, ir: ProcessIR) -> str:
        nodes = ir["nodes"]
        edges = ir["edges"]
        self._check_graph_shape(nodes, edges)

        node_ids = {
            n["key"]: identifiers.block_id(ir["process"]["name"], n["key"], self._physical(n))
            for n in nodes
        }
        coords = layout.compute_layout(nodes, edges)

        blocks: list[str] = []
        for z, node in enumerate(nodes, start=1):
            blocks.append(self._designer_item(node, node_ids[node["key"]], coords[node["key"]], z))

        conns: list[str] = []
        for z, edge in enumerate(edges, start=1):
            conns.append(self._connection(edge, node_ids, z))

        props = self._properties(ir)

        crlf = "\r\n"
        doc = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<Root>",
            "  <DesignerItems>",
            *blocks,
            "  </DesignerItems>",
            "  <Connections>",
            *conns,
            "  </Connections>",
        ]
        if props:
            doc.append("  <Properties>")
            doc.extend(props)
            doc.append("  </Properties>")
        else:
            doc.append("  <Properties></Properties>")
        doc += [
            "  <PSCache></PSCache>",
            "  <Description></Description>",
            "  <InputArguments></InputArguments>",
            "  <OutputArguments></OutputArguments>",
            "</Root>",
        ]
        return crlf.join(doc)

    # --- детали ---

    def _physical(self, node: dict) -> str:
        from .ir import resolve_physical

        physical = resolve_physical(node["type"])
        if physical is None:
            raise CompileError(
                f"тип узла {node['type']!r} отсутствует в каталоге блоков "
                f"(assets/block_index.json); неизвестное действие оформляйте ToDo"
            )
        if physical not in self.templates:
            raise CompileError(f"нет шаблона для блока {physical} (тип {node['type']})")
        return physical

    def _check_graph_shape(self, nodes: list, edges: list) -> None:
        # Проверяем физические имена: Start/ReturnOrStop — реальные InternalName,
        # алиасы Stop и т.п. не должны обходить проверку.
        physical_types = [resolve_physical(n["type"]) or n["type"] for n in nodes]
        if physical_types.count("Start") != 1:
            raise CompileError("в графе должен быть ровно один Start (GRF-001)")
        if physical_types.count("ReturnOrStop") < 1:
            raise CompileError("нужен хотя бы один Stop/ReturnOrStop (GRF-002)")
        # порты: from_port должен быть выходом шаблона, to_port — входом
        node_by_key = {n["key"]: n for n in nodes}
        for e in edges:
            src = node_by_key[e["from"]]
            tpl = self.templates[self._physical(src)]
            outs = {p["name"] for p in self._ports(tpl) if int(p["max_out"]) > 0}
            ins = {p["name"] for p in self._ports(tpl) if int(p["max_in"]) > 0}
            if e["from_port"] not in outs:
                raise CompileError(f"узел {src['key']}: порт {e['from_port']} не является выходом")
            dst = node_by_key[e["to"]]
            tpl_dst = self.templates[self._physical(dst)]
            ins_dst = {p["name"] for p in self._ports(tpl_dst) if int(p["max_in"]) > 0}
            if e["to_port"] not in ins_dst:
                raise CompileError(f"узел {dst['key']}: порт {e['to_port']} не является входом")

    @staticmethod
    def _ports(template: dict) -> list[dict]:
        return template.get("ports_cache") or Compiler._parse_ports(template)

    @staticmethod
    def _parse_ports(template: dict) -> list[dict]:
        inner = ET.fromstring(template["content"])
        ports = []
        for c in inner.iter():
            if c.tag.split("}")[-1] == "ConnectorSettings":
                ports.append(
                    {"name": c.get("Name", ""), "max_in": c.get("MaxInConnections", "0"),
                     "max_out": c.get("MaxOutConnections", "0")}
                )
        return ports

    def _designer_item(self, node: dict, guid: str, xy: tuple[int, int], z: int) -> str:
        physical = self._physical(node)
        tpl = self.templates[physical]
        values = node_property_values(node)
        # DisplayName: явный display_name; у ToDo — имя отсутствующего действия (golden best_*)
        display = node.get("display_name")
        if not display and resolve_physical(node["type"]) == "ToDoBlock":
            display = node.get("unresolved_action")
        content = fill_template(tpl["content"], values, tpl["property_defaults"], display_name=display)
        ET.fromstring(content)  # well-formed проверка шаблона после подстановки
        w, h = tpl["default_size"]["width"], tpl["default_size"]["height"]
        lines = [
            "    <DesignerItem>",
            f"      <Left>{xy[0]}</Left>",
            f"      <Top>{xy[1]}</Top>",
            f"      <Width>{w}</Width>",
            f"      <Height>{h}</Height>",
            f"      <ID>{guid}</ID>",
            f"      <zIndex>{z}</zIndex>",
            "      <IsGroup>false</IsGroup>",
            "      <IsEnabled>true</IsEnabled>",
            "      <Description></Description>",
            "      <IsPSVerified>true</IsPSVerified>",
            f"      <ParentID>{ZERO_GUID}</ParentID>",
            "      <StorageSelectors></StorageSelectors>",
            f"      <Content>{xml_attr_escape(content)}</Content>",  # Content — текст, экранируем целиком
            "    </DesignerItem>",
        ]
        return "\r\n".join(lines)

    @staticmethod
    def _connection(edge: dict, node_ids: dict, z: int) -> str:
        lines = [
            "    <Connection>",
            f"      <SourceID>{node_ids[edge['from']]}</SourceID>",
            f"      <SinkID>{node_ids[edge['to']]}</SinkID>",
            f"      <SourceConnectorName>{edge['from_port']}</SourceConnectorName>",
            f"      <SinkConnectorName>{edge['to_port']}</SinkConnectorName>",
            "      <SourceArrowSymbol>None</SourceArrowSymbol>",
            "      <SinkArrowSymbol>Arrow</SinkArrowSymbol>",
            f"      <zIndex>{z}</zIndex>",
            "    </Connection>",
        ]
        return "\r\n".join(lines)

    @staticmethod
    def _properties(ir: ProcessIR) -> list[str]:
        out = []
        for var in ir["variables"]:
            value = initial_value(var["sherpa_type"], var.get("initial"))
            attrs = (
                f'Name="{xml_attr_escape(var["name"])}" Type="{var["sherpa_type"]}" '
                f'IsGlobal="{"True" if var.get("scope") == "global" else "False"}" '
                f'DefaultValue="{xml_attr_escape(value)}" Value="{xml_attr_escape(value)}"'
            )
            out.append(f"    <DiagramProperty {attrs} />")
        return out
