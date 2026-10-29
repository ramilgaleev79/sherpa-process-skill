"""Регрессии ревью: alias/physical, контейнеры, process_diff, change_impact, аудит."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "dev"))

from change_impact import SpecError, validate_spec  # noqa: E402
from plan_from_text import build_plan  # noqa: E402
from plan_to_ir import PlanCompiler  # noqa: E402
from process_diff import diff  # noqa: E402
from sherpa_xml import compiler, ir, layout, validator  # noqa: E402
from sherpa_xml.types import text  # noqa: E402
from structure_audit import defuse, hot_vars, model  # noqa: E402


def _ir(nodes, edges, variables=None):
    return {"process": {"name": "R", "mode": "release"},
            "variables": variables or [], "nodes": nodes, "edges": edges}


def _log(key):
    return {"key": key, "type": "Log", "properties": {"Value": '"x"'}}


def test_physical_names_are_first_class():
    variables = [{"key": "i", "name": "i", "sherpa_type": "Number"}]
    data = _ir(
        [{"key": "s", "type": "Start"},
         {"key": "f", "type": "For",
          "start_index": {"kind": "number_literal", "value": 1},
          "end_index": {"kind": "number_literal", "value": 3},
          "index_variable": "i"},
         _log("b"),
         {"key": "z", "type": "ReturnOrStop"}],
        [{"from": "s", "from_port": "Next", "to": "f", "to_port": "Input"},
         {"from": "f", "from_port": "Index", "to": "b", "to_port": "InputLeft"},
         {"from": "b", "from_port": "Next", "to": "f", "to_port": "Input"},
         {"from": "f", "from_port": "Next", "to": "z", "to_port": "Input"}],
        variables,
    )
    parsed = ir.ProcessIR.parse(data)
    report = validator.validate_ir(parsed, "release", templates=compiler.Compiler().templates)
    assert report["status"] == "passed", report["issues"]
    out = compiler.Compiler().compile(parsed)
    root = ET.fromstring(out)
    for item in root.iter("DesignerItem"):
        inner = ET.fromstring(item.findtext("Content"))
        if inner.get("InternalName") == "For":
            props = {p.get("Name"): p.get("Value") for p in inner.iter()
                     if p.tag.rsplit("}", 1)[-1] == "Property"}
            assert props["StartIndex"] == "1" and props["EndIndex"] == "3"
            break
    else:
        raise AssertionError("For не найден")


def test_container_without_error_blocks_release():
    data = _ir(
        [{"key": "s", "type": "Start"}, {"key": "t", "type": "TryCatch"},
         _log("b"), {"key": "z", "type": "Stop"}],
        [{"from": "s", "from_port": "Next", "to": "t", "to_port": "Input"},
         {"from": "t", "from_port": "Next", "to": "b", "to_port": "Input"},
         {"from": "t", "from_port": "Out", "to": "z", "to_port": "Input"},
         {"from": "b", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = validator.validate_ir(ir.ProcessIR.parse(data), "release")
    assert report["status"] == "failed"
    assert "SHP-GRF-020" in {i["code"] for i in report["issues"]}


@pytest.mark.parametrize("step", [None, {"kind": "number_literal", "value": 0}])
def test_for_bounds_block_release(step):
    node = {"key": "f", "type": "ForLoop", "index_variable": "i"}
    if step is not None:
        node["step"] = step
        node["end_index"] = {"kind": "number_literal", "value": 3}
    data = _ir(
        [{"key": "s", "type": "Start"}, node, _log("b"), {"key": "z", "type": "Stop"}],
        [{"from": "s", "from_port": "Next", "to": "f", "to_port": "Input"},
         {"from": "f", "from_port": "Index", "to": "b", "to_port": "InputLeft"},
         {"from": "b", "from_port": "Next", "to": "f", "to_port": "Input"},
         {"from": "f", "from_port": "Next", "to": "z", "to_port": "Input"}],
        [{"key": "i", "name": "i", "sherpa_type": "Number"}],
    )
    report = validator.validate_ir(ir.ProcessIR.parse(data), "release")
    assert report["status"] == "failed"
    assert "SHP-GRF-021" in {i["code"] for i in report["issues"]}
    with pytest.raises(compiler.CompileError):
        compiler.Compiler().compile(ir.ProcessIR.parse(data))


def test_process_diff_sees_variable_changes():
    base = {"blocks": {}, "edges": [], "description": "", "sections": {}}
    old = {**base, "variables": {"X": {"type": "Number", "default": "0",
                                       "value": "0", "global": "False"}}}
    new = {**base, "variables": {"X": {"type": "Number", "default": "99",
                                       "value": "99", "global": "False"}}}
    result = diff(old, new)
    assert result["identical"] is False
    assert result["changed_variables"] and result["changed_variables"][0]["name"] == "X"


def test_change_impact_rejects_typo_spec():
    blocks = {"a": {"id": "a", "internal": "Start", "display": "", "enabled": True, "props": {}}}
    with pytest.raises(SpecError):
        validate_spec({"remove_edge": []}, blocks)
    with pytest.raises(SpecError):
        validate_spec({"remove_blocks": ["missing"]}, blocks)


def test_structure_audit_depth_and_cyrillic_vars():
    def b(i, name, props=None):
        return {"id": i, "internal": name, "display": "", "enabled": True, "props": props or {}}
    blocks = {
        "s": b("s", "Start"), "f": b("f", "For", {"Index": "$Индекс"}),
        "a": b("a", "IfCondition", {"Condition": "$Индекс -gt 2"}),
        "c": b("c", "IfCondition", {"Condition": "$Индекс -eq 3"}),
        "z": b("z", "ReturnOrStop"),
    }
    edges = [("s", "Next", "f", "Input"), ("f", "Index", "a", "InputLeft"),
             ("a", "Yes", "c", "Input"), ("a", "No", "f", "Input"),
             ("c", "Next", "f", "Input"), ("f", "Next", "z", "Input")]
    m = model(blocks, edges)
    assert m["depth"] == {0: 3, 1: 2}
    assert not m["hubs"]
    assert any(v["var"] == "Индекс" for v in hot_vars(m))


def test_plan_from_text_datatable_is_compilable():
    plan = build_plan("Для каждого файла отправить письмо.")
    data = PlanCompiler(plan).compile()
    ir.ProcessIR.parse(data)


def test_text_literal_escapes_dollar():
    raw = "Цена $100"
    assert text.ps_unliteral(text.ps_literal(raw)) == raw
    assert "`$" in text.ps_literal(raw)


def test_structure_audit_output_catalog_is_write():
    blocks = {
        "s": {"id": "s", "internal": "MakeScreenshot", "display": "",
              "enabled": True, "props": {"ScreenshotFile": "$Shot"},
              "prop_catalog": {"ScreenshotFile": "Output"}},
    }
    writes, reads = defuse(blocks)
    assert "s" in writes["Shot"]
    assert "Shot" not in reads


def test_process_layer_rejects_unknown_block_and_fanout():
    import re
    raw = (REPO / "golden" / "real" / "diag01-start-stop.process").read_bytes()
    bogus = raw.replace(b'InternalName="Start"', b'InternalName="Bogus"', 1)
    assert bogus != raw
    assert "SHP-DOC-005" in {i["code"] for i in validator.validate_process_bytes(bogus)["issues"]}

    match = re.search(rb"    <Connection>.*?</Connection>\r?\n", raw, re.S)
    assert match is not None
    duplicated = raw[:match.end()] + match.group(0) + raw[match.end():]
    codes = {i["code"] for i in validator.validate_process_bytes(duplicated)["issues"]}
    assert "SHP-PORT-002" in codes


def test_layout_handles_cycles():
    nodes = [{"key": k} for k in ("s", "f", "b", "z")]
    edges = [
        {"from": "s", "from_port": "Next", "to": "f", "to_port": "Input"},
        {"from": "f", "from_port": "Index", "to": "b", "to_port": "InputLeft"},
        {"from": "b", "from_port": "Next", "to": "f", "to_port": "Input"},
        {"from": "f", "from_port": "Next", "to": "z", "to_port": "Input"},
    ]
    levels = layout._longest_path_levels(nodes, edges)
    assert levels["s"] == 0
    assert levels["f"] == 1
    assert levels["b"] > levels["f"]
    assert levels["z"] > levels["f"]
