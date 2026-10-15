"""Паттерны patterns/*/pattern.json — валидация фрагментов как IR.

Паттерн — эталонное решение из нескольких блоков (PLAN.md фаза 2.5).
Фрагмент обязан быть валидным IR: обрамляем Start/Stop и проверяем контрактом ir.py.
raw-черновики (status=DRAFT-extracted) проверяются только структурно.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from sherpa_xml import ir

PATTERNS = Path(__file__).resolve().parents[2] / "patterns"
CONFIRMED = [p for p in PATTERNS.glob("*/pattern.json") if json.loads(p.read_text(encoding="utf-8"))["meta"]["status"] == "confirmed"]


def test_confirmed_patterns_exist():
    assert {p.parent.name for p in CONFIRMED} >= {"retry-trycatch", "while-counter"}


@pytest.mark.parametrize("path", CONFIRMED, ids=lambda p: p.parent.name)
def test_confirmed_pattern_is_valid_ir_fragment(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    meta, frag = data["meta"], data["fragment"]
    assert meta["entry"] and meta["exit"] and meta["when_to_use"] and meta["description"]
    entry_key = meta["entry"].split(":")[0]
    exit_key = meta["exit"].split(":")[0]
    keys = {n["key"] for n in frag["nodes"]}
    assert entry_key in keys and exit_key in keys, "entry/exit должны указывать на узлы фрагмента"
    # стыковка: Start → entry, exit-порт → Stop
    full = {
        "process": {"name": f"__pattern_{meta['name']}", "mode": "draft"},
        "variables": frag["variables"],
        "nodes": frag["nodes"] + [{"key": "__start", "type": "Start"}, {"key": "__stop", "type": "Stop"}],
        "edges": frag["edges"]
        + [{"from": "__start", "to": entry_key, "to_port": "Input"}]
        + [
            {"from": k, "from_port": port, "to": "__stop", "to_port": "Input"}
            for k, port in [meta["exit"].split(":")] + ([meta["error_exit"].split(":")] if meta.get("error_exit") else [])
        ],
    }
    parsed = ir.ProcessIR.parse(full)  # упадёт с IRError при нарушении контракта
    assert len(parsed["nodes"]) == len(frag["nodes"]) + 2


def test_draft_patterns_are_structurally_readable():
    for path in PATTERNS.glob("*/pattern.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert {"meta", "fragment"} <= set(data)
        assert {"nodes", "edges"} <= set(data["fragment"])
