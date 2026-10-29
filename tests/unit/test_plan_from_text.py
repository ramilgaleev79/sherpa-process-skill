"""Baseline «описание → план»: правила распознавания циклов, условий и доменных блоков."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dev.plan_from_text import build_plan, description_from_process  # noqa: E402


def _types(plan: dict) -> list[str]:
    return [s["type"] for s in plan["steps"]]


def test_n_screenshots_becomes_for_loop():
    plan = build_plan("Сделать 5 снимков экрана и сохранить на диск.")
    loop = next(s for s in plan["steps"] if s["type"] == "repeat")
    assert loop["kind"] == "for" and loop["end"] == 5
    assert any(v["name"] == "Индекс" for v in plan["variables"])


def test_condition_cue_wraps_action():
    plan = build_plan("Если файл существует, отправить письмо клиенту.")
    cond = next(s for s in plan["steps"] if s["type"] == "condition")
    assert cond["then"] and cond["then"][0]["type"] == "action"
    assert cond["then"][0]["block"] == "SendMailMessage"


def test_foreach_cue_detected():
    plan = build_plan("Для каждой строки таблицы отправить уведомление.")
    loop = next(s for s in plan["steps"] if s["type"] == "repeat")
    assert loop["kind"] == "foreach_rows"
    assert {v["name"] for v in plan["variables"]} >= {"Таблица", "Строка"}


def test_domain_blocks_added():
    plan = build_plan("Бот Max: получить сообщение и отправить ответ.")
    blocks = {s.get("block") or s.get("then", [{}])[0].get("block") for s in plan["steps"]}
    assert any(b and b.startswith("Max") for b in blocks)


def test_no_verbs_falls_back_to_log():
    plan = build_plan("Сегодня хорошая погода.")
    assert plan["steps"][0]["block"] == "Log"


def test_description_from_process_reads_comment(tmp_path):
    text = "Робот собирает заказы из Excel и выгружает отчёт на диск."
    content = (f'<ToolboxItemSettings InternalName="Comment" Version="21" '
               f'DisplayName="{text}" />').replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    xml = (f'<?xml version="1.0" encoding="utf-8"?><Root><DesignerItems>'
           f'<DesignerItem><ID>c</ID><Content>{content}</Content></DesignerItem>'
           f'</DesignerItems><Connections /></Root>')
    path = tmp_path / "task.process"
    path.write_text(xml, encoding="utf-8")
    assert description_from_process(path) == text
