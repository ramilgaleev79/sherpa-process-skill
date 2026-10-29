"""Читатель `.process`: описания внутри файла, старые версии, разрывы связей.

Тесты синтетические (собирают минимальный `.process` в tmp), поэтому не зависят
от установленного Sherpa RPA и от конкретной машины.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dev.corpus_mine import collect  # noqa: E402
from dev.graph_compare import MIN_COMMENT_LEN, compare, read_process  # noqa: E402

BLOCK = ('<ToolboxItemSettings InternalName="{name}" Version="{version}" '
         'DisplayName="{display}" />')


def _process_xml(items: list[dict], connections: list[tuple[str, str]]) -> str:
    def item(i: dict) -> str:
        content = BLOCK.format(**i).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        return f"<DesignerItem><ID>{i['id']}</ID><Content>{content}</Content></DesignerItem>"

    conns = "".join(
        f"<Connection><SourceID>{a}</SourceID><SinkID>{b}</SinkID>"
        f"<SourceConnectorName>Next</SourceConnectorName>"
        f"<SinkConnectorName>Input</SinkConnectorName></Connection>"
        for a, b in connections)
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?><Root><DesignerItems>"
            + "".join(item(i) for i in items)
            + f"</DesignerItems><Connections>{conns}</Connections></Root>")


def _write(tmp_path: Path, name: str, items: list[dict], conns: list[tuple[str, str]]) -> Path:
    path = tmp_path / name
    path.write_text(_process_xml(items, conns), encoding="utf-8")
    return path


def test_comment_block_is_the_description(tmp_path):
    long_text = "Проект создаёт приходную накладную в 1С и выгружает отчёт на диск."
    items = [
        {"id": "s", "name": "Start", "version": "2", "display": "Старт"},
        {"id": "c", "name": "Comment", "version": "19", "display": long_text},
        {"id": "k", "name": "Comment", "version": "19", "display": "кратко"},
        {"id": "e", "name": "ReturnOrStop", "version": "35", "display": "Конец"},
    ]
    graph = read_process(_write(tmp_path, "task.process", items, [("s", "e")]))
    assert graph["comments"] == [long_text]          # короткий комментарий — не описание
    assert len(graph["comments"][0]) >= MIN_COMMENT_LEN
    assert graph["dangling"] == 0


def test_old_versions_and_unknown_blocks_detected(tmp_path):
    items = [
        {"id": "s", "name": "Start", "version": "2", "display": "Старт"},
        {"id": "x", "name": "MadeUpBlock", "version": "1", "display": "Сделать что-то"},
        {"id": "e", "name": "ReturnOrStop", "version": "35", "display": "Конец"},
    ]
    graph = read_process(_write(tmp_path, "old.process", items, [("s", "e")]))
    assert "Start" in graph["legacy"]                 # в библиотеке Start v10
    assert graph["unknown"] == ["MadeUpBlock"]
    assert "MadeUpBlock" in graph["isolated"]         # блок без связей, не комментарий


def test_dangling_connection_does_not_break_reading(tmp_path):
    items = [
        {"id": "s", "name": "Start", "version": "10", "display": "Старт"},
        {"id": "e", "name": "ReturnOrStop", "version": "113", "display": "Конец"},
    ]
    graph = read_process(_write(tmp_path, "broken.process", items, [("s", "ghost"), ("s", "e")]))
    assert graph["dangling"] == 1                     # связь на несуществующий ID
    assert graph["edges"] == {("Start", "Next", "ReturnOrStop", "Input")}


def test_removed_block_gets_replacement(tmp_path):
    items = [
        {"id": "s", "name": "Start", "version": "10", "display": "Старт"},
        {"id": "c", "name": "LaunchChrome", "version": "1", "display": "Запустить Chrome"},
        {"id": "e", "name": "ReturnOrStop", "version": "113", "display": "Конец"},
    ]
    graph = read_process(_write(tmp_path, "legacy.process", items, [("s", "c"), ("c", "e")]))
    assert graph["replacements"] == {"LaunchChrome": "LaunchBrowser"}


def test_compare_reports_reference_health(tmp_path):
    items = [
        {"id": "s", "name": "Start", "version": "2", "display": "Старт"},
        {"id": "e", "name": "ReturnOrStop", "version": "35", "display": "Конец"},
    ]
    graph = read_process(_write(tmp_path, "a.process", items, [("s", "e")]))
    report = compare(graph, graph)
    assert report["score"] == 1.0
    assert report["reference_health"]["legacy"] >= 1


def test_corpus_mine_reads_inline_description(tmp_path):
    items = [
        {"id": "s", "name": "Start", "version": "10", "display": "Старт"},
        {"id": "c", "name": "Comment", "version": "21",
         "display": "Робот отправляет письмо клиенту и сохраняет копию на диск."},
        {"id": "m", "name": "SendMailMessage", "version": "1", "display": "Отправить письмо клиенту"},
        {"id": "e", "name": "ReturnOrStop", "version": "113", "display": "Конец"},
    ]
    _write(tmp_path, "mail.process", items, [("s", "c"), ("c", "m"), ("m", "e")])
    report, described = collect([tmp_path])
    meta = report["meta"]
    assert meta["described_inline"] == 1 and meta["described_sidecar"] == 0
    assert described == ["mail"]
    assert report["verbs"]["отправить"]["blocks"].get("SendMailMessage") == 1
