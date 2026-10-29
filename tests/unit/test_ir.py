"""Тесты IR-контракта: round-trip, закрытость схемы, висячие ссылки.

Запуск: .venv/Scripts/python.exe -m pytest tests/unit -q
(или чистый stdlib: python -m unittest — тесты написаны на pytest-стиле,
 минимальная зависимость позволяет оба прогона через pytest в dev-venv)
"""

from __future__ import annotations

import pytest

from sherpa_xml import ir
from cli import TEMPLATE  # sys.path настраивается в tests/conftest.py


def test_template_is_valid_ir():
    parsed = ir.ProcessIR.parse(TEMPLATE)
    assert parsed["process"]["mode"] == "draft"
    assert [n["type"] for n in parsed["nodes"]] == ["Start", "Assign", "Stop"]
    # скелет без алгоритма: синтаксис конструкций живёт в metadata, а не в графе
    assert parsed["metadata"]["constructs"] and parsed["metadata"]["hint"]


def test_roundtrip_byte_stable(tmp_path):
    first = ir.ProcessIR.parse(TEMPLATE)
    dumped = first.dumps()
    second = ir.ProcessIR.parse(dumped)
    assert second.dumps() == dumped  # канонический JSON байт-стабилен


def test_unknown_key_rejected():
    bad = {"process": {"name": "x"}, "variables": [], "nodes": [], "edges": [], "oopsy": 1}
    with pytest.raises(ir.IRError, match="неизвестные ключи"):
        ir.ProcessIR.parse(bad)


def test_unknown_node_type_rejected():
    bad = {"nodes": [{"key": "n1", "type": "NoSuchBlockXyz"}], "edges": []}
    with pytest.raises(ir.IRError, match="каталог"):
        ir.ProcessIR.parse(bad)


def test_unknown_variable_type_rejected():
    bad = {"variables": [{"key": "v", "name": "X", "sherpa_type": "Uri"}], "nodes": [], "edges": []}
    with pytest.raises(ir.IRError, match="тип .* не подтверждён"):
        ir.ProcessIR.parse(bad)


def test_dangling_edge_rejected():
    bad = {"nodes": [{"key": "n1", "type": "Start"}], "edges": [{"from": "n1", "to": "ghost"}]}
    with pytest.raises(ir.IRError, match="несуществующий узел"):
        ir.ProcessIR.parse(bad)


def test_todo_requires_unresolved_action():
    ok = {"nodes": [{"key": "t", "type": "ToDo", "unresolved_action": "отправить письмо"}], "edges": []}
    ir.ProcessIR.parse(ok)  # без unresolved_action упадёт ниже
    bad = {"nodes": [{"key": "t", "type": "ToDo"}], "edges": []}
    with pytest.raises(ir.IRError, match="unresolved_action"):
        ir.ProcessIR.parse(bad)


def test_assign_target_must_be_declared():
    bad = {
        "nodes": [{"key": "a", "type": "Assign",
                   "assignments": [{"target": "Ghost", "expression": {"kind": "number_literal", "value": 1}}]}],
        "edges": [],
    }
    with pytest.raises(ir.IRError, match="не объявлен"):
        ir.ProcessIR.parse(bad)


def test_condition_requires_condition():
    bad = {"nodes": [{"key": "c", "type": "Condition"}], "edges": []}
    with pytest.raises(ir.IRError, match="обязательно condition"):
        ir.ProcessIR.parse(bad)


def test_compare_operator_whitelisted():
    good_expr = {"kind": "compare", "operator": "lt",
                 "left": {"kind": "number_literal", "value": 1},
                 "right": {"kind": "number_literal", "value": 2}}
    bad_expr = {**good_expr, "operator": "_LESS_THAN_"}
    with pytest.raises(ir.IRError, match="оператор"):
        ir.ProcessIR.parse({"nodes": [{"key": "c", "type": "Condition", "condition": bad_expr}], "edges": []})


def test_string_literal_is_plain_data():
    # value — сырой текст; кавычки внутри — легитимные данные, PS-литерал строит writer
    ok = ir.Expr({"kind": "string_literal", "value": 'Он сказал "Тест"'})
    assert ok["value"] == 'Он сказал "Тест"'
    with pytest.raises(ir.IRError):
        ir.Expr({"kind": "string_literal", "value": 42})


def test_invoke_expression_requires_expression():
    bad = {"nodes": [{"key": "ie", "type": "InvokeExpression",
                      "properties": {"Expression1": "Get-Date"}}], "edges": []}
    with pytest.raises(ir.IRError, match="expression"):
        ir.ProcessIR.parse(bad)
    ok = {"nodes": [{"key": "ie", "type": "InvokeExpression",
                     "expression": {"kind": "powershell", "value": "Get-Date"}}], "edges": []}
    parsed = ir.ProcessIR.parse(ok)
    assert parsed["nodes"][0]["expression"]["value"] == "Get-Date"


def test_unresolved_actions_accepted_but_dropped():
    # старые IR с этим ключом парсятся; реестр ToDo теперь генерируется при сборке
    data = {
        "nodes": [{"key": "s", "type": "Start"}, {"key": "e", "type": "Stop"}],
        "edges": [{"from": "s", "to": "e"}],
        "unresolved_actions": [{"action": "старое поле"}],
    }
    parsed = ir.ProcessIR.parse(data)
    assert "unresolved_actions" not in parsed
    assert "unresolved_actions" not in parsed.dumps()
