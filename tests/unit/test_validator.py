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
         "properties": {"Items": "$Ht", "Key": '"k"', "Value": '"v"'}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "cl"}, {"from": "cl", "to": "e"}]
    # без golden-шаблона блок не компилируется — ERROR во ВСЕХ профилях (не ложный зелёный)
    for profile in ("draft", "release", "strict"):
        report = _v(bad, profile)
        assert report["status"] == "failed", (profile, report["issues"])
        assert "SHP-BLK-010" in _codes(report)


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


# --- raw-свойства и PowerShell-дисциплина (пост-ревью архитектуры) ---


def test_typed_field_override_blocked_in_all_profiles():
    bad = _base()
    bad["nodes"][1]["properties"] = {"Expression1": "1+1"}  # Assign: типизированное поле
    bad["nodes"].append({"key": "c", "type": "Condition",
                         "condition": {"kind": "compare", "operator": "eq",
                                       "left": {"kind": "number_literal", "value": 1},
                                       "right": {"kind": "number_literal", "value": 1}},
                         "properties": {"Condition": "1 -eq 1"}})
    bad["edges"].append({"from": "a", "to": "c"})
    bad["edges"].append({"from": "c", "from_port": "Yes", "to": "e"})
    for profile in ("draft", "release", "strict"):
        report = _v(bad, profile)
        assert report["status"] == "failed", (profile, report["issues"])
        assert _codes(report) >= {"SHP-PROP-001"}


def test_invoke_expression_warns_in_draft_blocks_in_strict():
    with_ie = _base()
    with_ie["nodes"].insert(1, {"key": "ie", "type": "InvokeExpression",
                                "expression": {"kind": "powershell", "value": "Get-Date"}})
    with_ie["edges"].insert(1, {"from": "ie", "to": "a"})
    with_ie["edges"][0] = {"from": "s", "to": "ie"}
    draft = _v(with_ie, "draft")
    assert draft["status"] == "passed"                       # WARN — не блокирует
    assert "SEM-EXPR-013" in _codes(draft)
    strict = _v(with_ie, "strict")
    assert strict["status"] == "failed"
    assert _codes(strict) >= {"SEM-EXPR-013", "SEM-EXPR-012"}  # узел + powershell-kind


def test_freeform_powershell_sweep_covers_initial_and_forloop():
    bad = _base()
    bad["variables"][0]["initial"] = {"kind": "powershell", "value": "1"}  # Number-тип, kind невалиден для сборки, но для валидатора — PS
    bad["nodes"].append({"key": "f", "type": "ForLoop", "end_index": {"kind": "powershell", "value": "3"},
                         "index_variable": "Counter"})
    bad["edges"].append({"from": "e", "to": "f"})
    strict = _v(bad, "strict")
    assert strict["status"] == "failed"
    locs = {i["location"] for i in strict["issues"] if i["code"] == "SEM-EXPR-012"}
    assert any("initial" in loc for loc in locs) and any("end_index" in loc for loc in locs)


def test_forloop_index_reference_and_type():
    bad = _base()
    bad["variables"].append({"key": "v2", "name": "Caption", "sherpa_type": "Text",
                             "initial": {"kind": "string_literal", "value": "x"}})
    bad["nodes"].append({"key": "f", "type": "ForLoop", "index_variable": "Caption"})
    bad["nodes"].append({"key": "g", "type": "ForLoop", "index_variable": "Ghost"})
    bad["edges"].append({"from": "e", "to": "f"})
    bad["edges"].append({"from": "f", "to": "g"})
    codes = _codes(_v(bad, "draft"))
    assert "SEM-REF-001" in codes          # Ghost не объявлен
    assert "SEM-TYPE-002" in codes         # Caption — Text, а не Number


def test_invented_variable_type_in_process_bytes():
    import re
    raw = (REPO / "golden" / "real" / "diag03-assign-4vars.process").read_bytes()
    corrupted = re.sub(rb'(<DiagramProperty[^>]*\bType=")Number(")', rb"\1Float\2", raw, count=1)
    assert corrupted != raw, "фикстура должна содержать Number-переменную"
    report = validator.validate_process_bytes(corrupted)
    assert report["status"] == "failed"
    assert "SHP-VAR-007" in _codes(report)


# --- P0/P1 по внешнему ревью: strict действительно строгий, release — полный ---


def test_nested_powershell_blocked_in_strict():
    # powershell-операнд ВНУТРИ arithmetic/compare, не только в корне
    bad = _base()
    bad["nodes"][1]["assignments"][0]["expression"] = {
        "kind": "arithmetic", "operator": "add",
        "left": {"kind": "powershell", "value": "Get-Process"},
        "right": {"kind": "number_literal", "value": 1}}
    assert _v(bad, "draft")["status"] == "passed"          # WARN
    strict = _v(bad, "strict")
    assert strict["status"] == "failed" and "SEM-EXPR-012" in _codes(strict)

    cond = _base()
    cond["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "c", "type": "Condition",
         "condition": {"kind": "compare", "operator": "eq",
                       "left": {"kind": "powershell", "value": "Get-Process"},
                       "right": {"kind": "number_literal", "value": 1}}},
        {"key": "e", "type": "Stop"},
    ]
    cond["edges"] = [{"from": "s", "to": "c"},
                     {"from": "c", "from_port": "Yes", "to": "e"},
                     {"from": "c", "from_port": "No", "to": "e", "to_port": "InputLeft"}]
    assert _v(cond, "strict")["status"] == "failed"
    assert "SEM-EXPR-012" in _codes(_v(cond, "strict"))


def _delay_ir(time_value):
    d = _base()
    d["nodes"].insert(1, {"key": "d", "type": "Delay", "properties": {"Time": time_value}})
    d["edges"].insert(1, {"from": "d", "to": "a"})
    d["edges"][0] = {"from": "s", "to": "d"}
    return d


def test_raw_property_grammar_strict():
    code = _v(_delay_ir("(Get-Date).Second"), "strict")
    assert code["status"] == "failed" and "SEM-PROP-002" in _codes(code)
    assert _v(_delay_ir("(Get-Date).Second"), "draft")["status"] == "passed"  # WARN
    assert _v(_delay_ir("5"), "strict")["status"] == "passed"
    assert _v(_delay_ir("$Counter"), "strict")["status"] == "passed"


def test_validate_ir_with_templates_catches_build_surprises():
    from sherpa_xml import compiler
    templates = compiler.Compiler().templates

    unknown = _base()
    unknown["nodes"][1]["properties"] = {"Bogus": "1"}
    rep = validator.validate_ir(ir.ProcessIR.parse(unknown), "draft", templates=templates)
    assert "SHP-PROP-002" in _codes(rep) and rep["status"] == "failed"

    coll = _base()
    coll["nodes"].insert(1, {"key": "cl", "type": "CollectionSetItem",
                             "properties": {"Items": "$Ht", "Key": '"k"', "Value": '"v"'}})
    coll["edges"].insert(1, {"from": "cl", "to": "a"})
    coll["edges"][0] = {"from": "s", "to": "cl"}
    # SHP-BLK-010 — защита от частичной библиотеки: убираем шаблон блока
    partial = {k: v for k, v in templates.items() if k != "HashtableAddItem"}
    rep2 = validator.validate_ir(ir.ProcessIR.parse(coll), "release", templates=partial)
    assert "SHP-BLK-010" in _codes(rep2) and rep2["status"] == "failed"


def test_unconnected_branch_blocks_release():
    bad = _base()
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "c", "type": "Condition",
         "condition": {"kind": "compare", "operator": "eq",
                       "left": {"kind": "number_literal", "value": 1},
                       "right": {"kind": "number_literal", "value": 1}}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "c"}, {"from": "c", "from_port": "Yes", "to": "e"}]
    assert _v(bad, "draft")["status"] == "passed"           # WARN
    assert "SHP-GRF-020" in _codes(_v(bad, "draft"))
    rel = _v(bad, "release")
    assert rel["status"] == "failed" and "SHP-GRF-020" in _codes(rel)


def test_forloop_text_index_blocks_release():
    bad = _base()
    bad["variables"].append({"key": "v2", "name": "Cap", "sherpa_type": "Text",
                             "initial": {"kind": "string_literal", "value": "x"}})
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "a", "type": "Assign", "assignments": [
            {"target": "Counter", "expression": {"kind": "variable_reference", "variable": "Counter"}}]},
        {"key": "f", "type": "ForLoop", "index_variable": "Cap",
         "end_index": {"kind": "number_literal", "value": 3}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "a"}, {"from": "a", "to": "f"},
                    {"from": "f", "from_port": "Index", "to": "e"},
                    {"from": "f", "from_port": "Next", "to": "e"}]
    assert "SEM-TYPE-002" in _codes(_v(bad, "draft"))       # WARN
    assert _v(bad, "draft")["status"] == "passed"
    assert _v(bad, "release")["status"] == "failed"


def test_dictionary_initial_rejected():
    bad = _base()
    bad["variables"].append({"key": "v2", "name": "Dict", "sherpa_type": "Dictionary",
                             "initial": {"kind": "hashtable_literal", "value": []}})
    rep = _v(bad, "draft")
    assert "SEM-VAR-010" in _codes(rep) and rep["status"] == "failed"


def test_dead_ir_fields_warn():
    bad = _base()
    bad["nodes"][1]["branches"] = {"yes": "e", "no": "e"}
    assert "SHP-IR-001" in _codes(_v(bad, "draft"))
    assert _v(bad, "draft")["status"] == "passed"


# --- пост-валидация .process: структурный слой (последний рубеж) ---


def _golden_bytes(name="diag03-assign-4vars.process"):
    return (REPO / "golden" / "real" / name).read_bytes()


def test_process_layer_structural_hardening():
    import re as _re
    base = _golden_bytes()

    broken = base.replace(b"ToolboxItemSettings", b"ToolboxItemSettings broken", 1)
    assert "SHP-DOC-002" in _codes(validator.validate_process_bytes(broken))

    ids = _re.findall(rb"<ID>([^<]+)</ID>", base)
    dup = base.replace(b"<ID>" + ids[1] + b"</ID>", b"<ID>" + ids[0] + b"</ID>", 1)
    assert "SHP-ID-006" in _codes(validator.validate_process_bytes(dup))

    sec = _re.sub(rb'(<DiagramProperty[^>]*Type=")Text(")', rb"\1SecureString\2", base, count=1)
    sec = _re.sub(rb'(<DiagramProperty[^>]*Type="SecureString"[^>]*DefaultValue=")[^"]*(")',
                  rb"\1secret\2", sec, count=1)
    assert "SHP-VAR-010" in _codes(validator.validate_process_bytes(sec))

    nodv = _re.sub(rb"(<DiagramProperty[^>]*?) DefaultValue=\"[^\"]*\"", rb"\1", base, count=1)
    assert "SHP-VAR-009" in _codes(validator.validate_process_bytes(nodv))

    bogus = base.replace(b"<SourceConnectorName>Next</SourceConnectorName>",
                         b"<SourceConnectorName>Bogus</SourceConnectorName>", 1)
    assert "SHP-ID-007" in _codes(validator.validate_process_bytes(bogus))

    assert validator.validate_process_bytes(b"\xef\xbb\xbf<Root>\xff\xfe</Root>")["status"] == "failed"


# --- P0/P1 второго ревью: strict = release + типизация raw-грамматики ---


def test_strict_blocks_release_errors_too():
    # SHP-GRF-020 и SEM-TYPE-002 обязаны блокировать и в strict, а не только в release
    bad = _base()
    bad["variables"].append({"key": "v2", "name": "Cap", "sherpa_type": "Text",
                             "initial": {"kind": "string_literal", "value": "x"}})
    bad["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "c", "type": "Condition",
         "condition": {"kind": "compare", "operator": "eq",
                       "left": {"kind": "variable_reference", "variable": "Counter"},
                       "right": {"kind": "number_literal", "value": 1}}},
        {"key": "f", "type": "ForLoop", "index_variable": "Cap",
         "end_index": {"kind": "number_literal", "value": 3}},
        {"key": "e", "type": "Stop"},
    ]
    bad["edges"] = [{"from": "s", "to": "c"},
                    {"from": "c", "from_port": "Yes", "to": "f"},
                    {"from": "f", "from_port": "Next", "to": "e"}]  # нет No и нет Index
    strict = _v(bad, "strict")
    assert strict["status"] == "failed"
    codes = _codes(strict)
    assert "SHP-GRF-020" in codes and "SEM-TYPE-002" in codes


def test_raw_property_grammar_type_aware():
    from sherpa_xml import compiler
    templates = compiler.Compiler().templates

    def check(props, profile="strict"):
        d = _base()
        d["nodes"].insert(1, {"key": "x", "type": "Delay", "properties": props})
        d["edges"].insert(1, {"from": "x", "to": "a"})
        d["edges"][0] = {"from": "s", "to": "x"}
        return validator.validate_ir(ir.ProcessIR.parse(d), profile, templates=templates)

    ok = check({"Time": "5", "Units": "Seconds", "WriteLogMessage": "True"})
    assert ok["status"] == "passed", ok["issues"]
    assert check({"Units": "Bogus"})["status"] == "failed"          # Enum — только вариант
    assert check({"WriteLogMessage": "$True"})["status"] == "failed"  # Bool — True/False без $
    assert check({"WriteLogMessage": ""})["status"] == "passed"      # Bool — дефолт-пусто
    assert check({"Time": "(Get-Date).Second"})["status"] == "failed"  # Expression — операнд
    assert check({"Time": "(Get-Date).Second"}, profile="draft")["status"] == "passed"

    # ListSort.Direction — enum-свойство без строки-операнда
    d = _base()
    d["variables"].append({"key": "v2", "name": "Lst", "sherpa_type": "List",
                           "initial": {"kind": "list_literal", "value": []}})
    d["nodes"].append({"key": "ls", "type": "ListSort",
                       "properties": {"Items": "$Lst", "Direction": "Ascending"}})
    d["edges"] = [{"from": "s", "to": "a"}, {"from": "a", "to": "ls"}, {"from": "ls", "to": "e"}]
    rep = validator.validate_ir(ir.ProcessIR.parse(d), "strict", templates=templates)
    assert rep["status"] == "passed", rep["issues"]
    d["nodes"][-1]["properties"]["Direction"] = "$x"
    rep2 = validator.validate_ir(ir.ProcessIR.parse(d), "strict", templates=templates)
    assert rep2["status"] == "failed" and "SEM-PROP-002" in _codes(rep2)


def test_property_value_must_be_string():
    bad = _base()
    bad["nodes"].insert(1, {"key": "d", "type": "Delay", "properties": {"Time": 5}})
    bad["edges"].insert(1, {"from": "d", "to": "a"})
    bad["edges"][0] = {"from": "s", "to": "d"}
    rep = _v(bad, "draft")
    assert "SHP-PROP-003" in _codes(rep) and rep["status"] == "failed"


def test_dictionary_variable_always_rejected():
    bad = _base()
    bad["variables"].append({"key": "v2", "name": "Dict", "sherpa_type": "Dictionary"})
    rep = _v(bad, "draft")
    assert "SEM-VAR-010" in _codes(rep) and rep["status"] == "failed"


def test_process_layer_checks_value_too():
    import re as _re
    base = _golden_bytes()
    m = _re.search(rb'(<DiagramProperty[^>]*Type="Text"[^>]*Value=")([^"]*)(")', base)
    assert m, "фикстура должна содержать Text-переменную"
    broken = base[:m.start(2)] + b"!!!not-base64!!!" + base[m.end(2):]
    assert "SEM-SER-001" in _codes(validator.validate_process_bytes(broken))
    sec = _re.sub(rb'(<DiagramProperty[^>]*Type=")Text(")', rb"\1SecureString\2", base, count=1)
    sec = _re.sub(rb'(<DiagramProperty[^>]*Type="SecureString"[^>]*Value=")[^"]*(")',
                  rb"\1secret\2", sec, count=1)
    assert "SHP-VAR-010" in _codes(validator.validate_process_bytes(sec))


_TPL_CONTENT = (
    '<ToolboxItemSettings InternalName="Start" Version="10">'
    '<ConnectorSettings Name="Input" MaxInConnections="100" MaxOutConnections="0"/>'
    '<ConnectorSettings Name="Next" MaxInConnections="0" MaxOutConnections="1"/>'
    "</ToolboxItemSettings>"
)


def _mini_process(content: str, source_port: str = "Next", sink_port: str = "Input") -> bytes:
    esc = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '\ufeff<Root><DesignerItems>'
        f'<DesignerItem><ID>A</ID><Content>{esc}</Content></DesignerItem>'
        f'<DesignerItem><ID>B</ID><Content>{esc}</Content></DesignerItem>'
        "</DesignerItems>"
        "<Connections><Connection>"
        "<SourceID>A</SourceID><SinkID>B</SinkID>"
        f"<SourceConnectorName>{source_port}</SourceConnectorName>"
        f"<SinkConnectorName>{sink_port}</SinkConnectorName>"
        "</Connection></Connections></Root>"
    ).encode("utf-8")


def test_process_layer_connector_directions_and_block_identity():
    assert validator.validate_process_bytes(_mini_process(_TPL_CONTENT))["status"] == "passed"
    # Next — вход? нет: SourceConnectorName обязан быть выходом (max_out>0)
    assert "SHP-ID-007" in _codes(
        validator.validate_process_bytes(_mini_process(_TPL_CONTENT, source_port="Input")))
    # Input — не вход? нет: SinkConnectorName обязан быть входом (max_in>0)
    assert "SHP-ID-008" in _codes(
        validator.validate_process_bytes(_mini_process(_TPL_CONTENT, sink_port="Next")))
    # Content без InternalName / с некорректной версией
    no_name = _TPL_CONTENT.replace(' InternalName="Start"', "")
    assert "SHP-DOC-003" in _codes(validator.validate_process_bytes(_mini_process(no_name)))
    bad_ver = _TPL_CONTENT.replace('Version="10"', 'Version="abc"')
    assert "SHP-DOC-004" in _codes(validator.validate_process_bytes(_mini_process(bad_ver)))
