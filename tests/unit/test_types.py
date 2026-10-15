"""Фаза 3: тесты сериализаторов значений.

Главный тест — полный round-trip против РЕАЛЬНЫХ golden-контейнеров
(b05-text-variants: пустая строка, ascii, кириллица, кавычки, многострочность):
decode(golden) → encode → Base64 байт-в-байт.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from sherpa_xml.types import primitives, text
from golden import text_variables


# Golden-литералы `` `" ``-стиля: наш writer обязан воспроизводить их байт-в-байт.
BYTE_ROUNDTRIP = {"TextEmpty", "TextSimple", "TextRu", "TextQuotes"}


def test_text_roundtrip_against_golden():
    samples = text_variables()
    assert len(samples) >= 5, "golden b05 должен содержать все варианты Text"
    for name, b64 in samples:
        raw = text.decode(b64)
        re_encoded = text.encode(raw)
        if name in BYTE_ROUNDTRIP:
            assert re_encoded == b64, f"{name}: byte round-trip разошёлся для {raw!r}"
        else:
            # multiline-редактор Designer пишет `""`-стиль; наш writer — `"``-стиль.
            # Гарантия: семантическая стабильность (decode∘encode = тождество).
            assert text.decode(re_encoded) == raw, f"{name}: семантический round-trip разошёлся"


def test_known_literals():
    assert text.decode("AAEAAAD/////AQAAAAAAAAAGAQAAAAIiIgs=") == ""
    assert text.ps_literal("Test123") == '"Test123"'
    assert text.ps_literal("Он сказал \"Тест\"") == '"Он сказал `"Тест`""'
    # multiline: наш writer — единый `"``-стиль
    lit = text.ps_literal('"Он сказал \n"Тест"')
    assert lit.startswith('"`"') and lit.endswith('`""')
    assert text.ps_unliteral(lit) == '"Он сказал \n"Тест"'
    # reader понимает `""`-стиль Designer (golden TextMultiline):
    # открывающая `""` = голая кавычка значения + удвоение-кавычка, хвост `""` аналогично
    golden_ml = '""Он сказал `n`"Тест`"""'
    assert text.ps_unliteral(golden_ml) == '"Он сказал \n"Тест""'


def test_prefix_constant():
    assert text.PREFIX == bytes.fromhex("0001000000ffffffff01000000000000000601000000")
    # константа не должна дрейфовать: снята с golden один раз
    raw = base64.b64decode("AAEAAAD/////AQAAAAAAAAAGAQAAAAIiIgs=")
    assert raw.startswith(text.PREFIX)


def test_sevenbit_roundtrip():
    for n in (0, 1, 2, 127, 128, 300, 16383, 16384, 1_000_000):
        enc = text.encode_7bit(n)
        value, offset = text.decode_7bit(enc, 0)
        assert value == n and offset == len(enc)


def test_decode_rejects_garbage():
    import pytest

    for bad in ("", "not-base64!!", base64.b64encode(b"xxx").decode()):
        with pytest.raises(ValueError):
            text.decode(bad)


def test_primitives_forms():
    assert primitives.number(0) == "0"
    assert primitives.number(10) == "10"
    assert primitives.number(3.5) == "3.5"
    assert primitives.number(4.0) == "4"
    assert primitives.logical_declaration(True) == "True"
    assert primitives.logical_declaration(False) == "False"
    assert primitives.logical_expression(True) == "$True"
    assert primitives.logical_expression(False) == "$False"
    assert primitives.secure_string_empty() == ""
