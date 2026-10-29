"""Блок-схема с багами: детерминизм, каноническая нумерация, валидация findings.

Ради этих тестов инструмент и написан как рендерер с контрактом: один `.process`
плюс один findings.json обязаны давать байт-в-байт один HTML, номера багов —
канонические (не зависят от порядка записей), а ссылка на несуществующий блок
роняет рендер до отрисовки, а не выпускает схему с висячей меткой.
"""

from __future__ import annotations

import json
from pathlib import Path

import cli
from flow_diagram import FindingError, main, render, resolve_ref, validate_findings


def _build(tmp_path: Path) -> Path:
    ir = {"process": {"name": "Flow", "mode": "draft"},
          "variables": [{"key": "x", "name": "X", "sherpa_type": "Number", "scope": "local",
                         "initial": {"kind": "number_literal", "value": 0}}],
          "nodes": [{"key": "s", "type": "Start"},
                    {"key": "a", "type": "Assign", "assignments": [
                        {"target": "X", "expression": {"kind": "number_literal", "value": 1}}]},
                    {"key": "m", "type": "Log"},
                    {"key": "z", "type": "Stop"}],
          "edges": [{"from": "s", "from_port": "Next", "to": "a", "to_port": "Input"},
                    {"from": "a", "from_port": "Next", "to": "m", "to_port": "Input"},
                    {"from": "m", "from_port": "Next", "to": "z", "to_port": "Input"}]}
    ir_path = tmp_path / "f.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "f.process"
    assert cli.cmd_build(ir_path, out, "draft") == 0, "фикстура должна собираться"
    return out


def _ids(process: Path) -> dict[str, str]:
    from flow_diagram import load_nodes
    blocks, _ = load_nodes(process)
    by_type = {b["internal"]: bid for bid, b in blocks.items()}
    return by_type


def _finding(where: list[str], severity: str = "P1", title: str = "Баг",
             fix: dict | None = None) -> dict:
    f = {"severity": severity, "title": title, "where": where,
         "what": "что сломано"}
    if fix is not None:
        f["fix"] = fix
    return f


def test_render_is_byte_deterministic(tmp_path):
    process = _build(tmp_path)
    first = render(process, None, None)
    second = render(process, None, None)
    assert first == second
    assert "f.process" in first and "svg" in first


def test_no_findings_render_is_plain_navigation(tmp_path):
    process = _build(tmp_path)
    out = tmp_path / "plain.flow.html"
    assert main([str(process), "--out", str(out), "--check"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "svg" in page and "багов" not in page
    assert "структура, без багов" not in page  # это вывод консоли, не страницы
    # в режиме без багов нет ни красных меток-кружков, ни легенды с номерами
    assert '<circle cx=' not in page


def test_findings_render_marks_and_numbers(tmp_path):
    process = _build(tmp_path)
    ids = _ids(process)
    findings = {"findings": [
        _finding([ids["Log"]], "P1", "Лог молчит"),
        _finding([ids["Assign"]], "P0", "Присваивание мимо",
                 fix={"text": "Проверять тип", "where": [ids["Assign"]],
                      "insert_after": [ids["Start"]],
                      "insert_title": "InvokeExpression: инициализация",
                      "insert_code": "$x = New-Object System.Collections.ArrayList"}),
    ]}
    fpath = tmp_path / "bugs.json"
    fpath.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "bugs.flow.html"
    assert main([str(process), "--findings", str(fpath), "--out", str(out),
                 "--check"]) == 0
    page = out.read_text(encoding="utf-8")
    # оба бага на одной картинке и в легенде с теми же номерами
    assert "Лог молчит" in page and "Присваивание мимо" in page
    assert page.count("НОВЫЙ БЛОК") == 1
    assert "System.Collections.ArrayList" in page
    assert "P0" in page and "P1" in page


def test_numbering_is_canonical_regardless_of_input_order(tmp_path):
    process = _build(tmp_path)
    ids = _ids(process)
    from flow_diagram import load_nodes
    blocks, _ = load_nodes(process)
    a = validate_findings({"findings": [_finding([ids["Log"]], "P1", "Б"),
                                        _finding([ids["Start"]], "P0", "А")]}, blocks)
    b = validate_findings({"findings": [_finding([ids["Start"]], "P0", "А"),
                                        _finding([ids["Log"]], "P1", "Б")]}, blocks)
    assert [f["num"] for f in a] == [f["num"] for f in b] == [1, 2]
    assert a[0]["severity"] == "P0" and b[0]["severity"] == "P0"
    # P0 выше P1 даже если его блок ниже по холсту
    lower = max(blocks, key=lambda i: blocks[i]["y"])
    upper = min(blocks, key=lambda i: blocks[i]["y"])
    c = validate_findings({"findings": [_finding([lower], "P0", "В"),
                                        _finding([upper], "P1", "Г")]}, blocks)
    assert c[0]["severity"] == "P0"


def test_unknown_block_ref_fails_before_render(tmp_path, capsys):
    process = _build(tmp_path)
    ids = _ids(process)
    fpath = tmp_path / "bad.json"
    fpath.write_text(json.dumps({"findings": [_finding(["deadbeef"])]},
                                ensure_ascii=False), encoding="utf-8")
    assert main([str(process), "--findings", str(fpath)]) == 1
    assert "FD-REF-001" in capsys.readouterr().err


def test_ambiguous_prefix_fails_with_candidates():
    fake = {"aaaaaaaa-1111": {}, "aaaaaaaa-2222": {}}
    try:
        resolve_ref("aaaaaaaa", fake)
        raise AssertionError("ожидали FD-REF-002")
    except FindingError as exc:
        assert "FD-REF-002" in str(exc) and "aaaaaaaa-2222" in str(exc)


def test_schema_violations(tmp_path):
    process = _build(tmp_path)
    from flow_diagram import load_nodes
    blocks, _ = load_nodes(process)
    for bad, code in [
        ([_finding([], )], "FD-SCHEMA-003"),                      # пустой where
        ([{"severity": "P9", "title": "т", "where": ["x" * 40], "what": "w"}],
         "FD-SCHEMA-004"),
        ([{"title": "нет severity", "where": ["x" * 40], "what": "w"}],
         "FD-SCHEMA-003"),
        ([_finding(["x" * 40], fix={"where": ["y" * 40]})], "FD-SCHEMA-005"),
    ]:
        try:
            validate_findings({"findings": bad}, blocks)
            raise AssertionError(f"ожидали {code} для {bad}")
        except FindingError as exc:
            assert code in str(exc), (code, str(exc))
    try:
        validate_findings({"nope": 1}, blocks)
        raise AssertionError("ожидали FD-SCHEMA-002")
    except FindingError as exc:
        assert "FD-SCHEMA-002" in str(exc)


def test_cli_subcommand_delegates(tmp_path):
    from cli import cmd_flow_diagram
    process = _build(tmp_path)
    out = tmp_path / "via-cli.html"
    assert cmd_flow_diagram(process, None, out, None, True) == 0
    assert out.exists()
