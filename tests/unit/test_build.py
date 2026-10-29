"""Конвейер build: валидация → компиляция → пост-валидация XML → артефакты.

Артефакты сборки: OUT, OUT.report.json (всегда), OUT.todo.json (при узлах ToDo).
Поле unresolved_actions в IR устарело — реестр собирается из узлов.
"""

from __future__ import annotations

import json

import cli

MINIMAL = {
    "process": {"name": "B", "mode": "draft"},
    "variables": [],
    "nodes": [{"key": "s", "type": "Start"}, {"key": "e", "type": "Stop"}],
    "edges": [{"from": "s", "to": "e"}],
}

WITH_TODO = {
    "process": {"name": "B", "mode": "draft"},
    "variables": [],
    "nodes": [
        {"key": "s", "type": "Start"},
        {"key": "t", "type": "ToDo", "unresolved_action": "Отправить письмо"},
        {"key": "e", "type": "Stop"},
    ],
    "edges": [{"from": "s", "to": "t"}, {"from": "t", "to": "e"}],
}


def _run(tmp_path, ir_dict, mode="draft"):
    ir_path = tmp_path / "p.ir.json"
    ir_path.write_text(json.dumps(ir_dict, ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "p.process"
    rc = cli.cmd_build(ir_path, out_path, mode)
    return rc, out_path


def test_build_writes_process_and_report(tmp_path, capsys):
    rc, out = _run(tmp_path, MINIMAL)
    assert rc == 0
    assert out.exists() and out.read_bytes().startswith(b"\xef\xbb\xbf")
    report = json.loads((tmp_path / "p.process.report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["deterministic"] is True
    assert report["todo_registry"] == [] and report["sha256"]
    assert not (tmp_path / "p.process.todo.json").exists()


def test_build_todo_registry_artifact(tmp_path):
    rc, out = _run(tmp_path, WITH_TODO)  # draft: ToDo допустим
    assert rc == 0
    todos = json.loads((tmp_path / "p.process.todo.json").read_text(encoding="utf-8"))
    assert todos == [{"node": "t", "action": "Отправить письмо", "display_name": "Отправить письмо"}]
    report = json.loads((tmp_path / "p.process.report.json").read_text(encoding="utf-8"))
    assert report["todo_registry"] == todos
    # release: ToDo блокирует выпуск; отчёт о провале всё равно пишется
    rel = tmp_path / "rel"
    rel.mkdir()
    rc2, out2 = _run(rel, WITH_TODO, mode="release")
    assert rc2 == 1
    assert not out2.exists()
    failed_report = json.loads((rel / "p.process.report.json").read_text(encoding="utf-8"))
    assert failed_report["status"] == "failed" and failed_report["output"] is None
    assert any(i["code"] == "SHP-TODO-001" for i in failed_report["issues"])


def test_build_compile_error_report(tmp_path):
    d = dict(MINIMAL)
    d["variables"] = [{"key": f"v{i}", "name": f"x{i}", "sherpa_type": "Number",
                       "initial": {"kind": "number_literal", "value": 0}} for i in range(11)]
    d["nodes"] = [
        {"key": "s", "type": "Start"},
        {"key": "a", "type": "Assign", "assignments": [
            {"target": f"x{i}", "expression": {"kind": "number_literal", "value": 1}}
            for i in range(11)]},  # лимит шаблона — 10 пар → CompileError
        {"key": "e", "type": "Stop"},
    ]
    d["edges"] = [{"from": "s", "to": "a"}, {"from": "a", "to": "e"}]
    rc, out = _run(tmp_path, d)
    assert rc == 1 and not out.exists()
    report = json.loads((tmp_path / "p.process.report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["stage"] == "compile"
    assert any(i["code"] == "SHP-CMP-001" for i in report["issues"])
