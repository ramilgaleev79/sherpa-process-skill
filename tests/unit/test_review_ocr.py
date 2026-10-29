"""Регрессия ревью на внешнем проекте-примере: числа, которые обязан находить линт.

Материал — внешний пример из 218 блоков (не хранится в репозитории; путь —
`OCR/main.process` в каталоге разработки). Если число изменилось — либо в примере
что-то починили (обновить ожидание осознанно), либо линт сломался.
Без файла примера тест пропускается.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from lint_process import lint
from structure_audit import load, model

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "OCR" / "main.process"

pytestmark = pytest.mark.skipif(not MAIN.exists(),
                                reason="проект OCR не входит в поставку скила")


@pytest.fixture(scope="module")
def report() -> dict:
    return lint(MAIN)


def test_secrets_are_found(report):
    # SMTP-пароль в открытом виде у трёх SendMailMessage (pyatч: включённый, выключенный
    # watchdog и клон из правки .review/patch.py — он скопировал пароль прототипа)
    assert report["summary"]["P0"] == 3
    secrets = [f for f in report["findings"] if f["code"] == "LNT-SEC-001"]
    assert all("Password" in f["message"] for f in secrets)
    assert not any(f["code"] == "LNT-SEC-002" for f in report["findings"]), \
        "http:// в этом проекте только у локальных адресов"


def test_structure_defects_are_found(report):
    codes = Counter(f["code"] for f in report["findings"])
    assert codes["LNT-GRF-001"] == 6, "недостижимые от Start блоки (мёртвый код)"
    assert codes["LNT-GRF-002"] == 0, "обрывов потока в проекте нет"
    assert codes["LNT-EDGE-004"] == 1, "выключенные блоки — одной находкой"
    assert "8 блоков" in next(f["message"] for f in report["findings"]
                              if f["code"] == "LNT-EDGE-004")


def test_interactive_and_legacy_are_reported(report):
    codes = Counter(f["code"] for f in report["findings"])
    assert codes["LNT-UI-001"] == 2, "MessageBox и FolderBrowserDialog в сервисе"
    assert codes["LNT-CAT-002"] == 1, "старые версии блоков — одной агрегированной находкой"
    assert codes["LNT-CAT-001"] == 0, "все блоки процесса есть в каталоге 1.4.4"


def test_goto_numbers_match_the_review(report):
    # в ревью: 6 GOTO-подобных стрелок (2 назад), 2 хаба, 8 выключенных блоков
    blocks, edges = load(MAIN)
    m = model(blocks, edges)
    goto = sum(len(v) for k, v in m["kinds"].items() if k.startswith("goto"))
    assert goto == 6
    assert len(m["hubs"]) == 2
    assert len(blocks) == 218 and len(edges) == 234


def test_report_is_json_ready(report):
    assert report["summary"] == {"P0": 3, "P1": 0, "P2": report["summary"]["P2"]}
    assert report["catalog_checked"] is True
    assert all({"code", "severity", "location", "message"} <= set(f)
               for f in report["findings"])


def test_expression_secrets_are_found():
    # ключ Groq (Bearer gsk_…) и токен DaData (hex≥32) лежат в теле InvokeExpression,
    # значение начинается с `$uri = …` — guard «ссылка на переменную» не должен
    # пропускать такие значения: это находки P0 реального ревью
    klass = lint(REPO / "OCR" / "классификация.process")
    assert klass["summary"]["P0"] == 1
    assert "Expression1" in next(f["message"] for f in klass["findings"]
                                 if f["code"] == "LNT-SEC-001")

    post = lint(REPO / "OCR" / "postprocessing.process")
    assert post["summary"]["P0"] == 1
    assert "hex" in next(f["message"] for f in post["findings"]
                         if f["code"] == "LNT-SEC-001")
