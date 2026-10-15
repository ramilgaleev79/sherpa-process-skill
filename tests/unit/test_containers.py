"""Фаза 3.2: round-trip контейнеров коллекций и DataTable против golden.

Каждый тест: golden Base64 → decode → encode → байт-в-байт совпадение.
Формулы выведены из этих же golden-образцов; побайтовое совпадение —
единственное доказательство корректности writer'а (confirmed-over-invented).
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

from sherpa_xml.types import collections, data_table

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "real"


def _var(filename: str, name: str) -> str:
    root = ET.parse(GOLDEN / filename).getroot()
    for p in root.findall(".//DiagramProperty"):
        if p.get("Name") == name:
            return p.get("DefaultValue", "")
    raise AssertionError(f"{filename}: переменная {name} не найдена")


def test_list_roundtrip():
    b64 = _var("b09-list-ops.process", "list")
    items = collections.decode_list(b64)
    assert items == ["1", "2", "3", "4", "5"]
    assert collections.encode_list(items) == b64


def test_hashtable_roundtrip():
    b64 = _var("b07-collections-filled.process", "HashSample")
    pairs = collections.decode_hashtable(b64)
    assert dict(pairs) == {"Second": "2", "First": "1", "Name": "Robot", "Third": "3"}
    assert collections.encode_hashtable(pairs) == b64


def test_datatable_roundtrip_all_three_states():
    for name, expect_cols in (("table1", 0), ("table2", 3), ("table3", 4)):
        b64 = _var("b11-datatables-x3.process", name)
        schema, diffgram = data_table.decode(b64)
        assert data_table.encode(schema, diffgram) == b64, f"{name}: round-trip разошёлся"
        assert schema.count("<xs:element name=") - 2 == expect_cols  # минус Table1 и tmpDataSet


def test_datatable_builders_reproduce_golden_xml():
    # пустая таблица = schema без xs:sequence + самозакрытый diffgram
    schema, diffgram = data_table.decode(_var("b11-datatables-x3.process", "table1"))
    assert data_table.build_schema(None) == schema
    assert data_table.build_diffgram(None) == diffgram


def test_datatable_builder_schema_with_columns():
    schema, _ = data_table.decode(_var("b11-datatables-x3.process", "table3"))
    rebuilt = data_table.build_schema(
        [("col1", "String"), ("col2", "Int32"), ("col3", "Decimal"), ("col4", "Guid")]
    )
    assert rebuilt == schema


def test_datatable_builder_diffgram_with_rows():
    _, diffgram = data_table.decode(_var("b11-datatables-x3.process", "table3"))
    rebuilt = data_table.build_diffgram(
        [{"col1": "1", "col2": "2", "col3": "3"}, {"col1": "4", "col2": "5", "col3": "6"}]
    )
    assert rebuilt == diffgram


def test_encode_decode_new_values_stable():
    # наши writer'ы на новых данных: encode → decode → encode байт-стабилен
    for items in ([], ["a"], ["Привет", "мир", "!"], [str(i) for i in range(9)]):
        b64 = collections.encode_list(items)
        assert collections.decode_list(b64) == items
        assert collections.encode_list(collections.decode_list(b64)) == b64
    pairs = [("к1", "з1"), ("k2", "v2")]
    b64 = collections.encode_hashtable(pairs)
    assert dict(collections.decode_hashtable(b64)) == dict(pairs)
    assert collections.encode_hashtable(collections.decode_hashtable(b64)) == b64
    b64 = data_table.encode(data_table.build_schema([("a", "String")]), data_table.build_diffgram([{"a": "x"}]))
    schema, diffgram = data_table.decode(b64)
    assert data_table.encode(schema, diffgram) == b64
