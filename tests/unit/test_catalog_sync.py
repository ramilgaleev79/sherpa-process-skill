"""Синхронизация IR ↔ библиотека блоков (752 блока из установленного Designer).

Библиотека генерируется `scripts/dev/harvest_catalog.py` из MainToolbox.xaml
(+ локализация). IR использует короткие алиасы (`NODE_TYPES`), но ими не ограничен:
тип узла может быть любым `InternalName` из `assets/block_index.json`.

Тесты держат два инварианта:
- каждый алиас указывает на существующий блок библиотеки;
- у каждого блока библиотеки есть шаблон для сборки.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from sherpa_xml.ir import BLOCK_NAMES, NODE_TYPES

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "references" / "block-catalog.yaml"
TEMPLATES = REPO / "assets" / "block_templates.json"
INDEX = REPO / "assets" / "block_index.json"


def test_every_ir_alias_exists_in_library():
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["blocks"]
    missing = [
        f"{logical}->{physical}"
        for logical, physical in NODE_TYPES.items()
        if physical not in catalog
    ]
    assert not missing, f"IR ссылается на блоки вне библиотеки: {missing}"


def test_every_library_block_has_template():
    templates = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    missing = sorted(set(BLOCK_NAMES) - set(templates))
    assert not missing, f"блоки библиотеки без шаблона сборки: {missing}"


def test_aliases_are_optional():
    """Обратная зависимость не требуется: большинство блоков живёт без алиаса."""
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["blocks"]
    without_alias = set(catalog) - set(NODE_TYPES.values())
    assert len(without_alias) > 100, (
        f"без логического имени всего {len(without_alias)} блоков — похоже, каталог урезан"
    )


def test_catalog_index_is_consistent():
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    meta = index["meta"]
    assert meta["count"] == len(index["blocks"]) == len(BLOCK_NAMES)
    assert meta["designer_toolbox_sha256"], "в индексе нет отпечатка MainToolbox.xaml"
    assert meta["palette_count"] and meta["palette_count"] <= meta["count"]


def test_catalog_matches_installed_designer():
    """Если Designer установлен — каталог не должен быть stale.

    В CI без Sherpa RPA тест пропускается; на рабочей машине ловит именно тот
    случай, когда block_templates.json остался от прошлой сборки.
    """
    xaml = Path.home() / "AppData" / "Roaming" / "Sherpa RPA" / "Designer" / "ExternalToolboxes" / "MainToolbox.xaml"
    if not xaml.exists():
        pytest.skip("Sherpa RPA Designer не установлен")
    import hashlib
    actual = hashlib.sha256(xaml.read_bytes()).hexdigest()[:12]
    meta = json.loads(INDEX.read_text(encoding="utf-8"))["meta"]
    assert meta["designer_toolbox_sha256"] == actual, (
        "каталог stale: перегенерируйте scripts/dev/harvest_catalog.py"
    )
