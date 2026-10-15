"""Фаза 5: валидатор — каждый код ловится на invalid-фикстуре и молчит на valid.

Плюс сквозная самопроверка: все 24 golden .process проходят .process-слой чисто
(это детектор регрессий декодеров на реальных файлах Designer 1.4.4).
"""

from __future__ import annotations

import sys
from pathlib import Path

from sherpa_xml import ir, validator

REPO = Path(__file__).resolve().parents[2]


def _v(ir_dict: dict, profile: str = "draft") -> dict:
    return validator.validate_ir(ir.ProcessIR.parse(ir_dict), profile)


def _codes(report: dict) -> set[str]:
    return {i["code"] for i in report["issues"]}


def _base(**nodes_extra) -> dict:
    d = {
        "process": {"name": "V", "mode": "draft"},
        "variables": [{"key": "v1", "name": "Counter", "sherpa_type": "Number",
                       "initial": {"kind": "number_literal", "value": 0}}],
        "nodes": [
            {"key": "s", "type": "Start"},
            {"key": "a", "type": "Assign", "assignments": [
                {"target": "Counter", "expression": {"kind": "variable_reference", "variable": "Counter"}}]},
            {"key": "e", "type": "Stop"},
        ],
        "edges": [
            {"from": "s", "to": "a"},
            {"from": "a", "to": "e"},
        ],
    }
    d.update(nodes_extra)
    return d


def test_valid_ir_passes_all_profiles():
    for profile in ("draft", "release", "strict"):
        report = _v(_base(), profile)
        assert report["status"] == "passed", (profile, report["issues"])


def test_todo_blocks_release_but_not_draft():
    with_todo = _base(nodes=[
        {"key": "s", "type": "Start"},
        {"key": "t", "type": "ToDo", "unresolved_action": "x"},
        {"key": "e", "type": "Stop"},
    ], edges=[{"from": "s", "to": "t"}, {"from": "t", "to": "e"}])
    assert _v(with_todo, "draft")["status"] == "passed"
    assert _codes(_v(with_todo, "draft")) >= {"SHP-TODO-001"}
    rel = _v(with_todo, "release")
    assert rel["status"] == "failed" and _codes(rel) >= {"SHP-TODO-001"}


def test_undeclared_variable_severity_by_profile():
    bad = _base()
    bad["nodes"][1]["assignments"][0]["expression"] = {
        "kind": "variable_reference", "variable": "Ghost"}
    assert "SEM-REF-001" in _codes(_v(bad, "draft"))          # WARN
    assert _v(bad, "draft")["status"] == "passed"
    assert _v(bad, "release")["status"] == "failed"            # ERROR в release


def test_external_variable_suppresses():
    ok = _base()
    ok["nodes"][1]["assignments"][0]["expression"] = {
        "kind": "variable_reference", "variable": "Ext"}
    ok["external_variables"] = [{"name": "Ext", "expected_type": "Number"}]
    assert "SEM-REF-001" not in _codes(_v(ok, "release"))


def test_type_mismatch():
    bad = _base()
    bad["nodes"][1]["assignments"][0] = {
        "target": "Counter",
        "expression": {"kind": "string_literal", "value": "не число"}}
    assert "SEM-TYPE-001" in _codes(_v(bad, "draft"))


def test_strict_rejects_untyped_condition():
    bad = _base()
    bad["variables"][0]["sherpa_type"] = "Text"
    bad["variables"][0]["initial"] = {"kind": "string_literal", "value": "x"}
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "c", "type": "Condition",
         "condition": {"kind": "variable_reference", "variable": "Counter"}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "c", "to_port": "Input"},
                    {"from": "c", "from_port": "Yes", "to": "e"},
                    {"from": "c", "from_port": "No", "to": "e", "to_port": "InputLeft"}]
    assert "SEM-EXPR-011" in _codes(_v(bad, "strict"))
    assert "implicit_truthiness" in {i["rule"] for i in _v(bad, "release")["issues"]}


def test_strict_rejects_powershell():
    bad = _base()
    bad["nodes"][1]["assignments"][0]["expression"] = {
        "kind": "powershell", "value": "Get-Date"}
    assert "SEM-EXPR-012" in _codes(_v(bad, "strict"))


def test_unreachable_node():
    bad = _base()
    bad["nodes"].append({"key": "orphan", "type": "ToDo", "unresolved_action": "x"})
    # orphan не подключён: GRF-010 + GRF-011 (терминальные исключены — ToDo не терминальный)
    codes = _codes(_v(bad, "draft"))
    assert "SHP-GRF-010" in codes and "SHP-GRF-011" in codes


def test_always_true_loop_warning():
    bad = _base()
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "w", "type": "WhileLoop",
         "condition": {"kind": "compare", "operator": "lt",
                       "left": {"kind": "number_literal", "value": 1},
                       "right": {"kind": "number_literal", "value": 2}}},
        {"key": "a", "type": "Assign", "assignments": [
            {"target": "Counter", "expression": {"kind": "arithmetic", "operator": "add",
                                                 "left": {"kind": "variable_reference", "variable": "Counter"},
                                                 "right": {"kind": "number_literal", "value": 1}}}]},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [
        {"from": "s", "to": "w"},
        {"from": "w", "from_port": "Iteration", "to": "a", "to_port": "InputLeft"},
        {"from": "a", "to": "w", "to_port": "Input"},
        {"from": "w", "from_port": "Next", "to": "e"},
    ]
    assert "SEM-LOOP-003" in _codes(_v(bad, "draft"))


def test_duplicate_edge():
    bad = _base()
    bad["edges"].append({"from": "s", "to": "a"})
    assert "SHP-GRF-012" in _codes(_v(bad, "draft"))


def test_unconfirmed_collection_block():
    bad = _base()
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "cl", "type": "CollectionSetItem",
         "properties": {"Items": "Ht", "Key": '"k"', "Value": '"v"'}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "cl"}, {"from": "cl", "to": "e"}]
    assert _v(bad, "draft")["status"] == "passed"      # WARN в draft
    assert _v(bad, "release")["status"] == "failed"    # forbidden в release


def test_unused_variable_warns():
    rep = _v(_base(), "draft")
    assert "SHP-VAR-008" not in _codes(rep)  # Counter используется


def test_all_golden_process_files_pass_process_layer():
    failed = []
    for path in sorted((REPO / "golden" / "real").glob("*.process")):
        report = validator.validate_process(path)
        if report["status"] != "passed":
            failed.append((path.name, report["issues"]))
    assert not failed, failed
