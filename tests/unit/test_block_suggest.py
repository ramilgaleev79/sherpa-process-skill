"""Корпусная подсказка блока: индекс «глагол → блок» и рантайм-поиск кандидатов.

Индекс — ассет (`assets/verb_block_index.json`), поэтому тесты держат три вещи:
сборка детерминирована, нормализация глаголов работает («скопировать» →
«копировать»), и блоки вне каталога 1.4.4 никогда не попадают в кандидаты.
"""

from __future__ import annotations

import json
from pathlib import Path

from block_suggest import INDEX, SKIP_BLOCKS, _normalize, match_verbs, suggest, main as suggest_main
from dev.build_verb_index import build


def _ir(display: str) -> dict:
    return {
        "process": {"name": "A", "mode": "draft"},
        "variables": [],
        "nodes": [{"key": "s", "type": "Start"},
                  {"key": "b", "type": "Log", "display_name": display},
                  {"key": "z", "type": "Stop"}],
        "edges": [{"from": "s", "from_port": "Next", "to": "b", "to_port": "Input"},
                  {"from": "b", "from_port": "Next", "to": "z", "to_port": "Input"}],
    }


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.ir.json").write_text(json.dumps(_ir("Отправить письмо клиенту"), ensure_ascii=False),
                                      encoding="utf-8")
    (corpus / "b.ir.json").write_text(json.dumps(_ir("Отправить отчёт"), ensure_ascii=False),
                                      encoding="utf-8")
    (corpus / "task.md").write_text("Отправить письмо клиенту.", encoding="utf-8")
    return corpus


def test_index_asset_exists_and_has_verbs():
    assert INDEX.exists(), "ассет assets/verb_block_index.json должен быть в репозитории"
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    assert data["meta"]["verbs"] >= 20
    assert data["verbs"]["отправить"]["blocks"], data["verbs"]["отправить"]


def test_build_is_deterministic_and_counts_display_names(tmp_path):
    corpus = _corpus(tmp_path)
    first = build([corpus])
    second = build([corpus])
    assert first == second
    assert first["verbs"]["отправить"]["blocks"]["Log"] == 2
    assert first["meta"]["processes"] == 2


def test_normalization_matches_aspect_pairs():
    assert _normalize("скопировать") == _normalize("копировать")
    assert _normalize("прочитать") == _normalize("читать")
    verbs = {"копировать": {}, "отправить": {}}
    assert match_verbs("скопировать файл", verbs) == ["копировать"]
    assert match_verbs("отправить письмо", verbs) == ["отправить"]


def test_suggest_returns_catalog_blocks_with_frequencies():
    result = suggest("отправить письмо", limit=5)
    assert result["verbs"] == ["отправить"]
    blocks = [row["block"] for row in result["candidates"]]
    assert "SendMailMessage" in blocks
    assert all(row["in_catalog"] for row in result["candidates"])


def test_suggest_never_proposes_blocks_outside_catalog():
    # в корпусе «запустить» часто означает LaunchChrome (блока нет в 1.4.4)
    result = suggest("запустить браузер", limit=10)
    blocks = [row["block"] for row in result["candidates"]]
    assert "LaunchChrome" not in blocks
    assert "LaunchChrome" in [row["block"] for row in result["unknown_blocks"]]


def test_service_blocks_are_not_candidates():
    result = suggest("сделать скриншот", limit=10)
    assert not SKIP_BLOCKS & {row["block"] for row in result["candidates"]}


def test_fallback_searches_catalog_by_display_name():
    # «скриншот» — не глагол: кандидатов должен дать каталог, а не пустой ответ
    result = suggest("скриншот", limit=3)
    assert result["fallback"] == "catalog"
    assert result["candidates"], "поиск по подписи блока не нашёл MakeScreenshot"
    assert "MakeScreenshot" in [c["block"] for c in result["candidates"]]


def test_cli_exit_code_reflects_found_candidates(capsys):
    assert suggest_main(["отправить письмо", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "отправить письмо"
