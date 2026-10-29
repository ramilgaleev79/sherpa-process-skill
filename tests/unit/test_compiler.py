"""Фаза 4: сквозной прогон IR → .process.

Проверки:
- well-formed результат, правильная структура секций;
- Content каждого блока парсится и содержит подставленные значения;
- переменные сериализуются подтверждёнными writer'ами и читаются обратно;
- граф: связи ↔ ID блоков ↔ порты;
- детерминизм байт-в-байт;
- паритет полей с golden diag01 (Start→Stop).
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sherpa_xml import compiler, ir
from sherpa_xml.types import text

REPO = Path(__file__).resolve().parents[2]


def _compile(ir_dict: dict) -> tuple[str, ET.Element]:
    parsed = ir.ProcessIR.parse(ir_dict)
    result = compiler.Compiler().compile(parsed)
    return result, ET.fromstring(result.encode("utf-8"))


MINIMAL = {
    "process": {"name": "T", "mode": "draft"},
    "variables": [],
    "nodes": [{"key": "s", "type": "Start"}, {"key": "e", "type": "Stop"}],
    "edges": [{"from": "s", "to": "e"}],
}


def test_minimal_process_structure():
    result, root = _compile(MINIMAL)
    assert result.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "\r\n" in result
    assert [c.tag for c in root] == [
        "DesignerItems", "Connections", "Properties",
        "PSCache", "Description", "InputArguments", "OutputArguments",
    ]
    items = root.findall("DesignerItems/DesignerItem")
    conns = root.findall("Connections/Connection")
    assert len(items) == 2 and len(conns) == 1
    inner_start = ET.fromstring(items[0].find("Content").text)
    inner_stop = ET.fromstring(items[1].find("Content").text)
    assert inner_start.get("InternalName") == "Start"
    assert inner_stop.get("InternalName") == "ReturnOrStop"
    src, dst = conns[0].findtext("SourceID"), conns[0].findtext("SinkID")
    assert src == items[0].findtext("ID") and dst == items[1].findtext("ID")
    assert conns[0].findtext("SourceConnectorName") == "Next"
    assert conns[0].findtext("SinkConnectorName") == "Input"


def test_determinism_byte_equal():
    a = compiler.Compiler().compile(ir.ProcessIR.parse(MINIMAL))
    b = compiler.Compiler().compile(ir.ProcessIR.parse(MINIMAL))
    assert a == b


FULL = {
    "process": {"name": "Full", "mode": "draft"},
    "variables": [
        {"key": "v1", "name": "Counter", "sherpa_type": "Number", "initial": {"kind": "number_literal", "value": 0}},
        {"key": "v2", "name": "Flag", "sherpa_type": "Logical", "initial": {"kind": "boolean_literal", "value": True}},
        {"key": "v3", "name": "Msg", "sherpa_type": "Text", "initial": {"kind": "string_literal", "value": 'Он сказал "Тест"'}},
        {"key": "v4", "name": "Lst", "sherpa_type": "List", "initial": {"kind": "list_literal", "value": ["1", "2"]}},
        {"key": "v5", "name": "Ht", "sherpa_type": "Hashtable", "initial": {"kind": "hashtable_literal", "value": [["k", "v"]]}},
        {"key": "v6", "name": "Tbl", "sherpa_type": "DataTable",
         "initial": {"kind": "data_table_literal", "value": {"columns": [["c1", "String"]], "rows": [{"c1": "x"}]}}},
    ],
    "nodes": [
        {"key": "start", "type": "Start"},
        {"key": "assign", "type": "Assign", "assignments": [
            {"target": "Counter",
             "expression": {"kind": "arithmetic", "operator": "add",
                            "left": {"kind": "variable_reference", "variable": "Counter"},
                            "right": {"kind": "number_literal", "value": 1}}}]},
        {"key": "cond", "type": "Condition",
         "condition": {"kind": "compare", "operator": "eq",
                       "left": {"kind": "variable_reference", "variable": "Flag"},
                       "right": {"kind": "boolean_literal", "value": True}}},
        {"key": "while1", "type": "WhileLoop",
         "condition": {"kind": "compare", "operator": "lt",
                       "left": {"kind": "variable_reference", "variable": "Counter"},
                       "right": {"kind": "number_literal", "value": 3}}},
        {"key": "stop", "type": "Stop"},
    ],
    "edges": [
        {"from": "start", "to": "assign"},
        {"from": "assign", "to": "while1"},
        {"from": "while1", "from_port": "Iteration", "to": "cond", "to_port": "InputLeft"},
        {"from": "cond", "from_port": "Yes", "to": "while1", "to_port": "Input"},
        {"from": "while1", "from_port": "Next", "to": "stop"},
    ],
}


def _props_of(root, node_key: str) -> dict[str, str]:
    for di in root.findall("DesignerItems/DesignerItem"):
        inner = ET.fromstring(di.find("Content").text)
        vals = {}
        for p in inner.iter():
            if p.tag.split("}")[-1] == "Property" and p.get("Value"):
                vals[p.get("Name")] = p.get("Value")
        return vals
    raise AssertionError(node_key)


def test_full_process_values_and_variables():
    result, root = _compile(FULL)
    # Assign: Variable1=$Counter, Expression1=$Counter + 1
    items = root.findall("DesignerItems/DesignerItem")
    inners = {ET.fromstring(i.find("Content").text).get("InternalName"): ET.fromstring(i.find("Content").text) for i in items}
    assign_vals = {}
    for p in inners["Assign"].iter():
        if p.tag.split("}")[-1] == "Property":
            assign_vals[p.get("Name")] = p.get("Value")
    assert assign_vals["Variable1"] == "$Counter"
    assert assign_vals["Expression1"] == "$Counter + 1"
    while_vals = {}
    for p in inners["While"].iter():
        if p.tag.split("}")[-1] == "Property":
            while_vals[p.get("Name")] = p.get("Value")
    assert while_vals["Condition"] == "$Counter -lt 3"
    cond_vals = {}
    for p in inners["IfCondition"].iter():
        if p.tag.split("}")[-1] == "Property":
            cond_vals[p.get("Name")] = p.get("Value")
    assert cond_vals["Condition"] == "$Flag -eq $True"

    # переменные: значения читаются нашими декодерами
    dp = {p.get("Name"): p for p in root.findall("Properties/DiagramProperty")}
    assert dp["Counter"].get("DefaultValue") == "0"
    assert dp["Flag"].get("DefaultValue") == "True"
    assert text.decode(dp["Msg"].get("DefaultValue")) == 'Он сказал "Тест"'
    from sherpa_xml.types import collections, data_table as dt

    assert collections.decode_list(dp["Lst"].get("DefaultValue")) == ["1", "2"]
    assert collections.decode_hashtable(dp["Ht"].get("DefaultValue")) == [("k", "v")]
    schema, diffgram = dt.decode(dp["Tbl"].get("DefaultValue"))
    assert 'name="c1"' in schema and "<c1>x</c1>" in diffgram


def test_port_validation_rejects_bad_port():
    bad = {
        "nodes": [{"key": "s", "type": "Start"}, {"key": "e", "type": "Stop"}],
        "edges": [{"from": "s", "from_port": "Iteration", "to": "e"}],  # у Start нет Iteration
    }
    with pytest.raises(compiler.CompileError, match="не является выходом"):
        _compile(bad)


def test_requires_start_and_stop():
    no_stop = {"nodes": [{"key": "s", "type": "Start"}], "edges": []}
    with pytest.raises(compiler.CompileError, match="Stop"):
        _compile(no_stop)


def test_parity_with_golden_diag01():
    """Паритет полей с golden (ID/координаты различны — проверяем структуру и шаблоны)."""
    golden = ET.parse(REPO / "golden" / "real" / "diag01-start-stop.process").getroot()
    _, mine = _compile(MINIMAL)
    g_items = golden.findall("DesignerItems/DesignerItem")
    m_items = mine.findall("DesignerItems/DesignerItem")
    assert len(g_items) == len(m_items)
    for g, m in zip(g_items, m_items):
        assert g.find("IsGroup").text == m.find("IsGroup").text == "false"
        assert g.find("IsPSVerified").text == m.find("IsPSVerified").text == "true"
        assert g.find("ParentID").text == m.find("ParentID").text
        g_inner = ET.fromstring(g.find("Content").text)
        m_inner = ET.fromstring(m.find("Content").text)
        assert g_inner.get("InternalName") == m_inner.get("InternalName")
        assert g_inner.get("Version") == m_inner.get("Version")
    g_conn = golden.findall("Connections/Connection")[0]
    m_conn = mine.findall("Connections/Connection")[0]
    for field in ("SourceConnectorName", "SinkConnectorName", "SourceArrowSymbol", "SinkArrowSymbol"):
        assert g_conn.findtext(field) == m_conn.findtext(field)


def test_todo_display_name_is_action():
    ir_dict = {
        "process": {"name": "T", "mode": "draft"},
        "variables": [],
        "nodes": [
            {"key": "s", "type": "Start"},
            {"key": "t", "type": "ToDo", "unresolved_action": "Сделать снимок экрана"},
            {"key": "e", "type": "Stop"},
        ],
        "edges": [{"from": "s", "to": "t"}, {"from": "t", "to": "e"}],
    }
    _, root = _compile(ir_dict)
    for di in root.findall("DesignerItems/DesignerItem"):
        inner = ET.fromstring(di.find("Content").text)
        if inner.get("InternalName") == "ToDoBlock":
            assert inner.get("DisplayName") == "Сделать снимок экрана"
            return
    raise AssertionError("ToDoBlock не найден")


def test_display_name_for_any_node():
    ir_dict = {
        "process": {"name": "T", "mode": "draft"},
        "variables": [{"key": "v", "name": "Index", "sherpa_type": "Number",
                       "initial": {"kind": "number_literal", "value": 0}}],
        "nodes": [
            {"key": "s", "type": "Start"},
            {"key": "c", "type": "Condition", "display_name": "$Index -gt 2",
             "condition": {"kind": "compare", "operator": "gt",
                           "left": {"kind": "variable_reference", "variable": "Index"},
                           "right": {"kind": "number_literal", "value": 2}}},
            {"key": "e", "type": "Stop"},
        ],
        "edges": [{"from": "s", "to": "c"},
                  {"from": "c", "from_port": "Yes", "to": "e"},
                  {"from": "c", "from_port": "No", "to": "e", "to_port": "InputLeft"}],
    }
    _, root = _compile(ir_dict)
    for di in root.findall("DesignerItems/DesignerItem"):
        inner = ET.fromstring(di.find("Content").text)
        if inner.get("InternalName") == "IfCondition":
            assert inner.get("DisplayName") == "$Index -gt 2"
            return
    raise AssertionError("IfCondition не найден")


def test_invoke_expression_typed_path():
    d = dict(MINIMAL)
    d["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "ie", "type": "InvokeExpression",
         "expression": {"kind": "powershell", "value": "Get-Date -Format o"}},
        {"key": "e", "type": "Stop"},
    ]
    d["edges"] = [{"from": "s", "to": "ie"}, {"from": "ie", "to": "e"}]
    _, root = _compile(d)
    contents = [c.text for c in root.findall("DesignerItems/DesignerItem/Content")]
    assert any("Get-Date -Format o" in c for c in contents)


def test_properties_cannot_override_typed_fields():
    d = dict(MINIMAL)
    d["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "c", "type": "Condition",
         "condition": {"kind": "compare", "operator": "eq",
                       "left": {"kind": "number_literal", "value": 1},
                       "right": {"kind": "number_literal", "value": 1}},
         "properties": {"Condition": "1 -eq 1"}},
        {"key": "e", "type": "Stop"},
    ]
    d["edges"] = [{"from": "s", "to": "c"}, {"from": "c", "from_port": "Yes", "to": "e"}]
    with pytest.raises(compiler.CompileError, match="типизированное"):
        _compile(d)
