"""plan_to_ir: таблица шагов → IR. Строгая схема, проводка портов, коды ошибок плана.

Каждый тест фиксирует одно правило механизма:
- шаг = строка плана с цитатой (без цитаты — отказ);
- конструкция выбирается типом шага (action/condition/repeat/try/terminal/data);
- порты соединяются автоматически по подтверждённым правилам;
- ошибки плана имеют машинные коды PLAN-*.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dev.plan_to_ir import PlanCompiler, PlanError  # noqa: E402
from sherpa_xml import ir, validator  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "plan-screenshots5.json"


def _plan(steps: list[dict], variables: list[dict] | None = None) -> dict:
    return {"process": {"name": "T"}, "variables": variables or [], "steps": steps}


def _compile(plan: dict) -> dict:
    data = PlanCompiler(plan).compile()
    return data


def _edges(data: dict) -> set[tuple[str, str, str, str]]:
    return {(e["from"], e["from_port"], e["to"], e["to_port"]) for e in data["edges"]}


def _types(data: dict) -> list[str]:
    return [n["type"] for n in data["nodes"]]


def test_minimal_plan_builds_start_action_stop():
    data = _compile(_plan([{"id": "log", "quote": "записать в лог", "type": "action",
                            "block": "Log", "props": {"Value": '"ok"'}}]))
    assert _types(data) == ["Start", "Log", "Stop"]
    assert _edges(data) == {
        ("start_1", "Next", "log", "Input"),
        ("log", "Next", "__stop", "Input"),
    }
    ir.ProcessIR.parse(data)  # контракт IR соблюдён


def test_fixture_plan_is_valid_release_ir():
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data = _compile(plan)
    parsed = ir.ProcessIR.parse(data)
    report = validator.validate_ir(parsed, "release")
    assert report["status"] == "passed", report["issues"]


def test_for_loop_wiring_matches_corpus_convention():
    data = _compile(_plan([
        {"id": "loop", "quote": "3 раза", "type": "repeat", "kind": "for",
         "start": 1, "end": 3, "index": "i",
         "body": [{"id": "a", "quote": "шаг", "type": "action", "block": "Log",
                   "props": {"Value": '"x"'}}]},
    ], [{"name": "i", "sherpa_type": "Number", "initial": 0}]))
    edges = _edges(data)
    assert ("loop", "Index", "a", "InputLeft") in edges      # тело входит через InputLeft
    assert ("a", "Next", "loop", "Input") in edges           # возврат в Input цикла
    assert ("loop", "Next", "__stop", "Input") in edges      # выход после цикла


def test_condition_no_branch_enters_input_left():
    data = _compile(_plan([
        {"id": "c", "quote": "если", "type": "condition",
         "expr": {"kind": "compare", "operator": "eq",
                  "left": {"kind": "number_literal", "value": 1},
                  "right": {"kind": "number_literal", "value": 1}}},
    ]))
    edges = _edges(data)
    assert ("c", "Yes", "__stop", "Input") in edges
    assert ("c", "No", "__stop", "InputLeft") in edges


def test_empty_branch_goes_straight_to_follow():
    data = _compile(_plan([
        {"id": "c", "quote": "если", "type": "condition",
         "expr": {"kind": "boolean_literal", "value": True},
         "then": [{"id": "t", "quote": "да", "type": "action", "block": "Log",
                   "props": {"Value": '"y"'}}]},
    ]))
    edges = _edges(data)
    assert ("c", "Yes", "t", "Input") in edges
    assert ("c", "No", "__stop", "InputLeft") in edges
    assert ("t", "Next", "__stop", "Input") in edges


def test_todo_action_becomes_placeholder():
    data = _compile(_plan([{"id": "x", "quote": "сделать снимок", "type": "action",
                            "todo": "Сделать снимок экрана"}]))
    node = next(n for n in data["nodes"] if n["key"] == "x")
    assert node["type"] == "ToDo" and node["unresolved_action"] == "Сделать снимок экрана"


def test_determinism_same_plan_same_ir():
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    first = json.dumps(_compile(plan), ensure_ascii=False, sort_keys=True)
    second = json.dumps(_compile(plan), ensure_ascii=False, sort_keys=True)
    assert first == second


@pytest.mark.parametrize("steps,variables,code", [
    ([{"id": "a", "quote": "x", "type": "action", "block": "Log", "props": {"Value": '"1"'}},
      {"id": "a", "quote": "y", "type": "action", "block": "Log", "props": {"Value": '"2"'}}],
     None, "PLAN-001"),
    ([{"id": "a", "type": "action", "block": "Log"}], None, "PLAN-002"),
    ([{"id": "a", "quote": "q", "type": "action", "block": "NoSuchBlock"}], None, "PLAN-005"),
    ([{"id": "a", "quote": "q", "type": "action", "block": "Log",
       "props": {"Bogus": "1"}}], None, "PLAN-008"),
    ([{"id": "a", "quote": "q", "type": "action", "block": "MakeScreenshot",
       "props": {"FileName": "$Нет"}}], None, "PLAN-009"),
    ([{"id": "r", "quote": "цикл", "type": "repeat", "kind": "for", "start": 1,
       "end": 2, "index": "i", "body": []}], [{"name": "i", "sherpa_type": "Number"}], "PLAN-006"),
    ([{"id": "t", "quote": "конец", "type": "terminal"},
      {"id": "a", "quote": "после конца", "type": "action", "block": "Log"}], None, "PLAN-012"),
])
def test_plan_errors_have_codes(steps, variables, code):
    with pytest.raises(PlanError) as exc:
        _compile(_plan(steps, variables))
    assert exc.value.code == code


def test_action_on_error_wires_block_error_port():
    # graph-synthesis, шаг 5: «сообщить об ошибке» → Error → Log, затем продолжение
    data = _compile(_plan([
        {"id": "read", "quote": "прочитать файл", "type": "action", "block": "FileRead",
         "props": {"FileName": '"a.txt"'},
         "on_error": [{"id": "warn", "quote": "сообщить об ошибке", "type": "action",
                       "block": "Log", "props": {"Value": '"ошибка чтения"'}}]},
    ]))
    edges = _edges(data)
    assert ("read", "Error", "warn", "InputLeft") in edges
    assert ("read", "Next", "__stop", "Input") in edges
    assert ("warn", "Next", "__stop", "Input") in edges


def test_action_on_error_requires_error_port():
    with pytest.raises(PlanError) as exc:
        _compile(_plan([{"id": "log", "quote": "записать", "type": "action", "block": "Log",
                         "on_error": [{"id": "x", "quote": "обработать", "type": "action",
                                       "block": "Log"}]}]))
    assert exc.value.code == "PLAN-008"


def test_action_on_error_must_be_non_empty():
    with pytest.raises(PlanError) as exc:
        _compile(_plan([{"id": "read", "quote": "прочитать", "type": "action",
                         "block": "FileRead", "props": {"FileName": '"a.txt"'},
                         "on_error": []}]))
    assert exc.value.code == "PLAN-006"
