"""Синхронизация ir.NODE_TYPES ↔ references/block-catalog.yaml.

Каталог генерируется из golden (gen_catalog.py); IR — закрытый контракт.
Тест гарантирует, что каждый физический блок IR существует в каталоге,
кроме явно перечисленных main.md-confirmed / golden-missing.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from sherpa_xml.ir import NODE_TYPES

# Подтверждены main.md §12.2/§18.1, но пока не встречены в golden/real
# (нужны 1–2 диаграммы с операциями Hashtable/Dictionary).
GOLDEN_MISSING = {
    "CollectionClear": "HashtableClear",
    "CollectionSetItem": "HashtableAddItem",
    "CollectionRemoveItem": "HashtableRemoveItem",
    "CollectionContainsKey": "HashtableContainsKey",
    "CollectionContainsValue": "HashtableContainsValue",
}

CATALOG = Path(__file__).resolve().parents[2] / "references" / "block-catalog.yaml"


def test_every_ir_block_exists_in_catalog():
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["blocks"]
    missing = [
        f"{logical}->{physical}"
        for logical, physical in NODE_TYPES.items()
        if physical not in catalog and logical not in GOLDEN_MISSING
    ]
    assert not missing, f"IR ссылается на блоки вне каталога: {missing}"


def test_golden_missing_list_is_still_relevant():
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["blocks"]
    appeared = {k: v for k, v in GOLDEN_MISSING.items() if v in catalog}
    assert not appeared, (
        f"Эти блоки появились в golden — уберите их из GOLDEN_MISSING и проверьте порты: {appeared}"
    )
