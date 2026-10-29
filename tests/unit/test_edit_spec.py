"""Спецификация правки: применение к файлу и доказательство «дифф = спека».

Проверяется главное свойство инструмента: файл не переписывается «на глаз» —
новые блоки клонируются из этого же файла, нетронутые блоки остаются
байт-в-байт, а расхождение диффа со спецификацией останавливает запись.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli
from apply_spec import ApplyError, apply, main as apply_main
from edit_spec import SpecError, compare_to_spec, escape_content, validate_spec
from process_diff import diff, load, main as diff_main

BASE_IR = {
    "process": {"name": "Edit", "mode": "draft"},
    "variables": [],
    "nodes": [
        {"key": "s", "type": "Start"},
        {"key": "l", "type": "Log", "properties": {"Value": '"a"'}},
        {"key": "z", "type": "Stop"},
    ],
    "edges": [
        {"from": "s", "from_port": "Next", "to": "l", "to_port": "Input"},
        {"from": "l", "from_port": "Next", "to": "z", "to_port": "Input"},
    ],
}
NEW_ID = "11111111-1111-4111-8111-111111111111"


def _base(tmp_path: Path) -> Path:
    ir_path = tmp_path / "p.ir.json"
    ir_path.write_text(json.dumps(BASE_IR, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "p.process"
    assert cli.cmd_build(ir_path, out, "draft") == 0
    return out


def _by_name(path: Path) -> dict[str, str]:
    model = load(path)
    return {b["internal"]: bid for bid, b in model["blocks"].items()}


def _spec(path: Path) -> dict:
    ids = _by_name(path)
    return {
        "add_blocks": [{
            "id": NEW_ID, "internal": "Log", "display": "Проверить файл",
            "clone_of": ids["Log"], "props": {"Value": '"b"'},
            "place": {"anchor": ids["Log"]},
        }],
        "remove_edges": [[ids["Start"], "Next", ids["Log"], "Input"]],
        "add_edges": [[ids["Start"], "Next", NEW_ID, "Input"],
                      [NEW_ID, "Next", ids["Log"], "Input"]],
        "set_props": [{"block": ids["Log"], "name": "Level", "value": '"Info"'}],
    }


def _run(tmp_path: Path, spec: dict, **kwargs) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_apply_adds_clone_and_keeps_untouched_blocks(tmp_path):
    process = _base(tmp_path)
    before = load(process)
    original_bom = process.read_bytes().startswith(b"\xef\xbb\xbf")
    spec = _spec(process)
    touched = {sp["block"] for sp in spec["set_props"]}
    res = apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
                force_backup=False, allow_new_issues=False)
    assert res["written"] and res["check"]["ok"]
    assert (tmp_path / "p.bak").exists()

    after = load(process)
    assert NEW_ID in after["blocks"]
    new_block = after["blocks"][NEW_ID]
    assert new_block["internal"] == "Log"
    assert new_block["display"] == "Проверить файл"
    assert new_block["props"]["Value"] == '"b"'
    # нетронутые блоки — байт-в-байт (включая Content и отступы)
    for bid, block in before["blocks"].items():
        if bid in touched:
            continue
        assert after["blocks"][bid]["raw"] == block["raw"]
    assert process.read_bytes().startswith(b"\xef\xbb\xbf") == original_bom


def test_apply_requires_backup(tmp_path):
    process = _base(tmp_path)
    with pytest.raises(ApplyError, match="бэкап"):
        apply(process, _spec(process), backup=None, dry_run=False,
              force_backup=False, allow_new_issues=False)


def test_apply_refuses_to_overwrite_other_backup(tmp_path):
    process = _base(tmp_path)
    backup = tmp_path / "p.bak"
    backup.write_bytes(b"\xef\xbb\xbfother")
    with pytest.raises(ApplyError, match="бэкап"):
        apply(process, _spec(process), backup=backup, dry_run=False,
              force_backup=False, allow_new_issues=False)


def test_apply_dry_run_does_not_touch_file(tmp_path):
    process = _base(tmp_path)
    original = process.read_bytes()
    spec = _spec(process)
    res = apply(process, spec, backup=None, dry_run=True,
                force_backup=False, allow_new_issues=False)
    assert res["dry_run"] and res["check"]["ok"]
    assert process.read_bytes() == original


def test_apply_leaves_file_intact_on_mismatch(tmp_path):
    process = _base(tmp_path)
    original = process.read_bytes()
    spec = _spec(process)
    spec["remove_edges"].append([_by_name(process)["Log"], "Next",
                                 _by_name(process)["ReturnOrStop"], "Input"])
    spec["add_edges"].append([_by_name(process)["Log"], "Next", NEW_ID, "Input"])
    spec["set_props"].append({"block": _by_name(process)["Log"], "name": "Value", "value": '"zzz"'})
    with pytest.raises((ApplyError, SpecError)):
        apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
              force_backup=False, allow_new_issues=False)
    assert process.read_bytes() == original


def test_apply_removes_block_and_its_edges(tmp_path):
    process = _base(tmp_path)
    ids = _by_name(process)
    spec = {
        "add_blocks": [{"id": NEW_ID, "internal": "Log", "clone_of": ids["Log"]}],
        "remove_edges": [[ids["Log"], "Next", ids["ReturnOrStop"], "Input"]],
        "add_edges": [[ids["Log"], "Next", NEW_ID, "Input"],
                      [NEW_ID, "Next", ids["ReturnOrStop"], "Input"]],
    }
    apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    removal = {"remove_blocks": [NEW_ID],
               "remove_edges": [[ids["Log"], "Next", NEW_ID, "Input"]],
               "add_edges": [[ids["Log"], "Next", ids["ReturnOrStop"], "Input"]]}
    res = apply(process, removal, backup=tmp_path / "p2.bak", dry_run=False,
                force_backup=False, allow_new_issues=False)
    assert res["check"]["ok"]
    after = load(process)
    assert NEW_ID not in after["blocks"]
    assert not [e for e in after["edges"] if NEW_ID in (e["SourceID"], e["SinkID"])]


def test_apply_rejects_clone_of_other_block_type(tmp_path):
    process = _base(tmp_path)
    ids = _by_name(process)
    spec = {"add_blocks": [{"id": NEW_ID, "internal": "Delay", "clone_of": ids["Log"]}]}
    with pytest.raises(ApplyError, match="клон"):
        apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
              force_backup=False, allow_new_issues=False)


def test_clone_of_disabled_block_is_enabled(tmp_path):
    process = _base(tmp_path)
    raw = process.read_bytes().decode("utf-8-sig")
    # выключаем Log-прототип, как это делает Designer, и клонируем его
    process.write_bytes(b"\xef\xbb\xbf" + raw.replace("<IsEnabled>true</IsEnabled>",
                                                      "<IsEnabled>false</IsEnabled>", 1).encode("utf-8"))
    ids = _by_name(process)
    spec = {"add_blocks": [{"id": NEW_ID, "internal": "Log", "clone_of": ids["Log"]}]}
    apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    text = process.read_bytes().decode("utf-8-sig")
    start = text.rindex("<DesignerItem>", 0, text.index(NEW_ID))
    item_text = text[start:text.index("</DesignerItem>", text.index(NEW_ID))]
    assert "<IsEnabled>true</IsEnabled>" in item_text


def test_compare_to_spec_reports_missing_and_extra(tmp_path):
    process = _base(tmp_path)
    spec = _spec(process)
    check = compare_to_spec({"identical": True}, spec)
    assert not check["ok"] and len(check["missing"]) >= 4

    bogus = {"added_blocks": [], "removed_blocks": [], "changed_blocks": [
        {"id": _by_name(process)["Log"], "internal": "Log", "display": "Лог",
         "deltas": [{"kind": "tag", "name": "Top", "old": "1", "new": "2"}]}],
        "added_edges": [], "removed_edges": []}
    check = compare_to_spec(bogus, {})
    assert not check["ok"] and check["unexpected"]


def test_process_diff_expect_cli_exit_codes(tmp_path):
    process = _base(tmp_path)
    original = tmp_path / "orig.process"
    original.write_bytes(process.read_bytes())
    spec_path = _run(tmp_path, _spec(process))
    apply(process, _spec(process), backup=tmp_path / "p.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    assert diff_main([str(original), str(process), "--expect", str(spec_path)]) == 0

    other = {"set_props": [{"block": _by_name(process)["Start"], "name": "Value",
                            "value": '"x"'}]}
    other_path = _run(tmp_path, other)
    assert diff_main([str(original), str(process), "--expect", str(other_path)]) == 1


def test_apply_cli_end_to_end(tmp_path):
    process = _base(tmp_path)
    spec_path = _run(tmp_path, _spec(process))
    assert apply_main([str(process), "--spec", str(spec_path), "--backup"]) == 0
    assert (tmp_path / "p.process.bak").exists()
    assert NEW_ID in {i for i in load(process)["blocks"]}


def test_wildcard_remove_edge_matches_concrete_diff(tmp_path):
    # спецификация может не знать точный порт: [src, null, dst, null] — «любая связь между»
    process = _base(tmp_path)
    ids = _by_name(process)
    spec = {
        "remove_edges": [[ids["Start"], None, ids["Log"], None]],
        "add_edges": [[ids["Start"], "Next", ids["ReturnOrStop"], "Input"]],
    }
    res = apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
                force_backup=False, allow_new_issues=False)
    assert res["check"]["ok"], res["check"]


def test_expect_verifies_props_of_new_block(tmp_path):
    # «--expect» обязан ловить файл, где клон получил не те свойства
    process = _base(tmp_path)
    ids = _by_name(process)
    spec = {"add_blocks": [{"id": NEW_ID, "internal": "Log", "clone_of": ids["Log"],
                            "display": "Проверка", "props": {"Value": '"b"'}}]}
    apply(process, spec, backup=tmp_path / "p.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    d = diff(load(tmp_path / "p.bak"), load(process))
    assert compare_to_spec(d, spec)["ok"]

    lying = json.loads(json.dumps(spec))
    lying["add_blocks"][0]["props"]["Value"] = '"другое"'
    check = compare_to_spec(d, lying)
    assert not check["ok"]
    assert any("Value" in row for row in check["unexpected"])


def test_secret_value_is_not_printed_in_reports(tmp_path, capsys):
    # блок с секретным свойством: Password есть только у SendMailMessage
    ir = {
        "process": {"name": "Secret", "mode": "draft"},
        "variables": [],
        "nodes": [
            {"key": "s", "type": "Start"},
            {"key": "m", "type": "SendMailMessage",
             "properties": {"Server": '"smtp.example.org"', "MailTo": '"a@b.c"',
                            "Subject": '"s"', "Body": '"b"'}},
            {"key": "z", "type": "Stop"},
        ],
        "edges": [{"from": "s", "from_port": "Next", "to": "m", "to_port": "Input"},
                  {"from": "m", "from_port": "Next", "to": "z", "to_port": "Input"}],
    }
    ir_path = tmp_path / "secret.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    process = tmp_path / "secret.process"
    assert cli.cmd_build(ir_path, process, "draft") == 0
    ids = _by_name(process)
    spec = {"set_props": [{"block": ids["SendMailMessage"], "name": "Password",
                           "value": '"hunter2-secret"'}]}
    spec_path = _run(tmp_path, spec)
    capsys.readouterr()
    assert apply_main([str(process), "--spec", str(spec_path),
                       "--backup", str(tmp_path / "s.bak")]) == 0
    out = capsys.readouterr().out
    assert "секрет скрыт" in out
    assert "hunter2-secret" not in out


def test_apply_is_deterministic_for_same_input(tmp_path):
    # две одинаковые копии + одна спецификация → байт-в-байт одинаковый результат
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    first = _base(a_dir)
    second = tmp_path / "b" / "p.process"
    second.parent.mkdir()
    second.write_bytes(first.read_bytes())
    spec = _spec(second)
    apply(first, spec, backup=tmp_path / "a.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    apply(second, spec, backup=tmp_path / "b.bak", dry_run=False,
          force_backup=False, allow_new_issues=False)
    assert first.read_bytes() == second.read_bytes()


def test_set_props_on_added_block_is_rejected():
    # свойства нового блока задаются в add_blocks.props: иначе writer не знает,
    # куда их писать, и падал бы KeyError вместо понятного отказа
    blocks = {"a": {"id": "a", "internal": "Log"}}
    with pytest.raises(SpecError, match="add_blocks.props"):
        validate_spec({"add_blocks": [{"id": "b", "internal": "Log"}],
                       "set_props": [{"block": "b", "name": "Level", "value": '"Info"'}]},
                      blocks)


def test_duplicate_edges_in_spec_are_rejected():
    blocks = {"a": {"id": "a", "internal": "Start"},
              "b": {"id": "b", "internal": "Log"}}
    with pytest.raises(SpecError, match="дважды"):
        validate_spec({"remove_edges": [["a", "Next", "b", "Input"],
                                        ["a", "Next", "b", "Input"]]}, blocks)
    with pytest.raises(SpecError, match="дважды"):
        validate_spec({"add_edges": [["a", "Next", "b", "Input"],
                                     ["a", "Next", "b", "Input"]]}, blocks)


def test_backup_can_be_forced(tmp_path):
    process = _base(tmp_path)
    backup = tmp_path / "p.bak"
    backup.write_bytes(b"\xef\xbb\xbfstale")
    spec = _spec(process)
    with pytest.raises(ApplyError, match="--force-backup"):
        apply(process, spec, backup=backup, dry_run=False,
              force_backup=False, allow_new_issues=False)
    res = apply(process, spec, backup=backup, dry_run=False,
                force_backup=True, allow_new_issues=False)
    assert res["written"] and backup.read_bytes() != b"\xef\xbb\xbfstale"


def test_validate_spec_rejects_bad_place_and_duplicate_props():
    blocks = {"a": {"id": "a", "internal": "Start"}}
    with pytest.raises(SpecError):
        validate_spec({"add_blocks": [{"id": "b", "internal": "Start",
                                       "place": {"anchor": "nope"}}]}, blocks)
    with pytest.raises(SpecError):
        validate_spec({"add_blocks": [{"id": "b", "internal": "Start", "typo": 1}]}, blocks)
    with pytest.raises(SpecError):
        validate_spec({"set_props": [{"block": "a", "name": "X", "value": "1"},
                                     {"block": "a", "name": "X", "value": "2"}]}, blocks)


def test_escape_content_two_levels():
    assert escape_content('"Письмо"') == "&amp;quot;Письмо&amp;quot;"
