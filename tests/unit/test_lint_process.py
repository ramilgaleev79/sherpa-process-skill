"""Линт чужого .process: секреты, каталог, обработка ошибок, мёртвый код.

Тесты держат две вещи, ради которых инструмент и появился: (1) известные дефекты
реального ревью действительно ловятся одной командой, (2) на golden-эталонах нет
ложных P0/P1 — иначе линтом перестают пользоваться.
"""

from __future__ import annotations

import json
from pathlib import Path

import cli
from lint_process import lint, main as lint_main

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "real"


def _build(tmp_path: Path, nodes: list[dict], edges: list[dict],
           variables: list[dict] | None = None, mode: str = "draft") -> Path:
    ir = {"process": {"name": "Lint", "mode": mode},
          "variables": variables or [], "nodes": nodes, "edges": edges}
    ir_path = tmp_path / "p.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "p.process"
    assert cli.cmd_build(ir_path, out, mode) == 0, "база для теста должна собираться"
    return out


def _nodes(*extra: dict) -> list[dict]:
    return [{"key": "s", "type": "Start"}, *extra, {"key": "z", "type": "Stop"}]


def _codes(report: dict, severity: str | None = None) -> set[str]:
    return {f["code"] for f in report["findings"]
            if severity is None or f["severity"] == severity}


def test_golden_files_have_no_p0_or_p1():
    files = sorted(GOLDEN.glob("diag*.process")) + sorted(GOLDEN.glob("b*.process"))
    assert len(files) >= 10, "нужны golden-файлы: иначе проверка проходит вхолостую"
    for path in files:
        report = lint(path)
        assert report["summary"]["P0"] == 0, (path.name, report["findings"])
        assert report["summary"]["P1"] == 0, (path.name, report["findings"])


def test_secret_in_property_is_p0(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "m", "type": "SendMailMessage",
                "properties": {"Server": '"smtp.example.org"', "Password": '"05bBu8HG"',
                               "MailTo": '"a@b.c"', "Subject": '"x"', "Body": '"y"'}}),
        [{"from": "s", "from_port": "Next", "to": "m", "to_port": "Input"},
         {"from": "m", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    assert "LNT-SEC-001" in _codes(report, "P0")
    assert any("Password" in f["message"] for f in report["findings"] if f["code"] == "LNT-SEC-001")


def test_secret_patterns_are_detected(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "i", "type": "Log",
                "properties": {"Value": '"Bearer abcdefghijklmnop1234"',
                               "MakeScreenshot": '"gsk_ULNKPwqsiKvpw1Sjek6JWGdyb3FYtxHeVQoo"',
                               "SendOrkest": '"http://api.example.org/v1?token=4f0342584f11113b112dac7ae0d5055cb35ffccb"'}}),
        [{"from": "s", "from_port": "Next", "to": "i", "to_port": "Input"},
         {"from": "i", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    messages = " ".join(f["message"] for f in report["findings"] if f["code"] == "LNT-SEC-001")
    assert "Bearer" in messages and "Groq" in messages and "hex" in messages
    assert "LNT-SEC-002" in _codes(report, "P1")   # http:// во внешнем вызове


def test_secret_inside_ps_expression_is_found(tmp_path):
    # регрессия: тело InvokeExpression начинается с `$uri = …` — guard «значение =
    # ссылка на переменную» раньше гасил образцы, и ключ в теле скрипта не находился
    # (ключ Groq и токен DaData из реального ревью жили именно там)
    script = ('$uri = "https://api.example.org/v1";\n'
              '$h = @{"Authorization" = "Bearer gsk_ULNKPwqsiKvpw1Sjek6JWGdyb3FYtxHeVQoo"}')
    process = _build(
        tmp_path,
        _nodes({"key": "i", "type": "Log",
                "properties": {"Value": script, "MakeScreenshot": "$smtp_password"}}),
        [{"from": "s", "from_port": "Next", "to": "i", "to_port": "Input"},
         {"from": "i", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    messages = [f["message"] for f in report["findings"] if f["code"] == "LNT-SEC-001"]
    assert any("Groq" in m or "Bearer" in m for m in messages)
    assert all(not m.startswith("MakeScreenshot:") for m in messages), "$var-ссылка — не секрет"


def test_handle_without_error_port_is_p1(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "FileRead",
                "properties": {"FileName": '"[System.Environment]::CurrentDirectory + \'\\\\\\\\x\'"',
                               "ErrorLevel": "Handle"}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    assert "LNT-EDGE-002" in _codes(report, "P1")


def test_disabled_and_interactive_blocks_are_reported(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "m", "type": "MessageBox",
                "properties": {"Message": '"Продолжить?"', "Result": "$Result"}}),
        [{"from": "s", "from_port": "Next", "to": "m", "to_port": "Input"},
         {"from": "m", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    assert "LNT-UI-001" in _codes(report, "P2")
    # выключенный блок: правим IsEnabled в файле, как это делает Designer
    raw = process.read_bytes().decode("utf-8-sig")
    process.write_bytes(b"\xef\xbb\xbf" + raw.replace("<IsEnabled>true</IsEnabled>",
                                                      "<IsEnabled>false</IsEnabled>", 1).encode("utf-8"))
    report = lint(process)
    assert "LNT-EDGE-004" in _codes(report, "P2")


def test_missing_stop_path_and_dead_block(tmp_path):
    process = _build(
        tmp_path,
        [{"key": "s", "type": "Start"},
         {"key": "r1", "type": "Log", "properties": {"Value": '"a"'}},
         {"key": "r2", "type": "Log", "properties": {"Value": '"b"'}},
         {"key": "z", "type": "Stop"}],
        [{"from": "s", "from_port": "Next", "to": "r1", "to_port": "Input"},
         {"from": "r1", "from_port": "Next", "to": "r2", "to_port": "Input"},
         {"from": "r2", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    raw = process.read_bytes().decode("utf-8-sig")
    # разрываем Start → r1: r1/r2/z становятся мёртвым кодом (как «недостижимые» в ревью)
    start_conn = raw.index("<Connection>")
    end_conn = raw.index("</Connection>", start_conn) + len("</Connection>\n    ")
    broken = raw[:start_conn] + raw[end_conn:]
    process.write_bytes(b"\xef\xbb\xbf" + broken.encode("utf-8"))
    report = lint(process)
    assert "LNT-GRF-001" in _codes(report, "P2")


def test_dangling_edge_is_p0(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "Log", "properties": {"Value": '"a"'}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    raw = process.read_bytes().decode("utf-8-sig")
    broken = raw.replace("  </Connections>",
                         "    <Connection><SourceID>нет-такого</SourceID><SinkID>и-такого</SinkID>"
                         "<SourceConnectorName>Next</SourceConnectorName>"
                         "<SinkConnectorName>Input</SinkConnectorName></Connection>\n"
                         "  </Connections>", 1)
    process.write_bytes(b"\xef\xbb\xbf" + broken.encode("utf-8"))
    report = lint(process)
    assert "LNT-EDGE-001" in _codes(report, "P0")


def test_duplicate_block_id_is_p0(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "Log", "properties": {"Value": '"a"'}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    raw = process.read_bytes().decode("utf-8-sig")
    item_start = raw.index("<DesignerItem>") - 4
    item_end = raw.index("</DesignerItem>") + len("</DesignerItem>\n    ")
    duplicated = raw[:item_end] + raw[item_start:item_end] + raw[item_end:]
    process.write_bytes(b"\xef\xbb\xbf" + duplicated.encode("utf-8"))
    report = lint(process)
    assert "LNT-ID-001" in _codes(report, "P0")


def test_unknown_block_from_catalog_is_p1(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "Log", "properties": {"Value": '"a"'}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    raw = process.read_bytes().decode("utf-8-sig")
    # блок, которого нет в каталоге 1.4.4 (удалён и переименован)
    broken = raw.replace('InternalName=&quot;Log&quot;', 'InternalName=&quot;LaunchChrome&quot;', 1)
    process.write_bytes(b"\xef\xbb\xbf" + broken.encode("utf-8"))
    report = lint(process)
    assert "LNT-CAT-001" in _codes(report, "P1")


def test_cli_exit_codes_depend_on_fail_on(tmp_path, capsys):
    process = _build(
        tmp_path,
        _nodes({"key": "m", "type": "SendMailMessage",
                "properties": {"Password": '"secret"', "Server": '"s"',
                               "MailTo": '"a@b.c"', "Subject": '"s"', "Body": '"b"'}}),
        [{"from": "s", "from_port": "Next", "to": "m", "to_port": "Input"},
         {"from": "m", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    assert lint_main([str(process)]) == 1
    assert lint_main([str(process), "--fail-on", "none"]) == 0


def test_broken_file_is_reported_without_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.process"
    bad.write_text('<?xml version="1.0"?><Root><Connections></Connections></Root>',
                   encoding="utf-8")
    capsys.readouterr()
    assert lint_main([str(bad)]) == 2
    captured = capsys.readouterr()
    assert "не читается" in captured.err
    assert "Traceback" not in captured.err


def test_cli_lint_process_subcommand_delegates(tmp_path, capsys):
    import cli
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "Log", "properties": {"Value": '"a"'}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    capsys.readouterr()
    assert cli.cmd_lint_process([process], False, "p0", 5) == 0
    assert process.name in capsys.readouterr().out


def test_http_url_credentials_are_masked(tmp_path):
    process = _build(
        tmp_path,
        _nodes({"key": "i", "type": "Log",
                "properties": {"Value": '"http://user:pass@api.example.org/v1"'}}),
        [{"from": "s", "from_port": "Next", "to": "i", "to_port": "Input"},
         {"from": "i", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    report = lint(process)
    message = next(f["message"] for f in report["findings"] if f["code"] == "LNT-SEC-002")
    assert "user:pass" not in message and "***@" in message


def test_fail_on_p1_ignores_p0_only_rule(tmp_path, capsys):
    # P1 без P0: ErrorLevel=Handle без подключённого выхода Error
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "FileRead",
                "properties": {"FileName": '"[System.Environment]::CurrentDirectory"',
                               "ErrorLevel": "Handle"}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    capsys.readouterr()
    assert lint_main([str(process), "--fail-on", "p0"]) == 0
    assert lint_main([str(process), "--fail-on", "p1"]) == 1


def test_json_report_is_machine_readable(tmp_path, capsys):
    process = _build(
        tmp_path,
        _nodes({"key": "r", "type": "Log", "properties": {"Value": '"a"'}}),
        [{"from": "s", "from_port": "Next", "to": "r", "to_port": "Input"},
         {"from": "r", "from_port": "Next", "to": "z", "to_port": "Input"}],
    )
    capsys.readouterr()          # вывод сборки не относится к отчёту линта
    assert lint_main([str(process), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["summary"]["P0"] == 0 and payload[0]["summary"]["P1"] == 0
    assert payload[0]["catalog_checked"] is True
    assert payload[0]["structure"]["kinds"]
