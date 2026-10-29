"""Baseline «описание → план» без LLM: правила и словарь глаголов из корпуса.

Назначение — измерительный стенд, а не замена модели. Он показывает, сколько
структуры вытягивают *сами правила* (Шаги 1–4 методички) из текста описания:
цифры «N раз», «если», «для каждого», «пока» и глаголы из словаря.

Всё, что требует понимания смысла (какие именно данные, какие условия, какие
свойства), здесь сознательно не угадывается: условия — заглушка `True`,
свойства пустые. Поэтому score такого плана — нижняя граница, а не оценка скилла.

Запуск:
  python scripts/dev/plan_from_text.py --from-process task.process --out plan.json
  python scripts/dev/plan_from_text.py --text task.md --print
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Глагол → блок (верхний уровень словаря; из него же строится таблица шагов)
VERB_BLOCKS = {
    "отправить": "SendMailMessage", "послать": "SendMailMessage",
    "сохранить": "CopyItem", "скопировать": "CopyItem", "переместить": "CopyItem",
    "удалить": "RemoveItem",
    "открыть": "LaunchApp", "закрыть": "CloseApp",
    "запустить": "StartProcess", "остановить": "StartProcess",
    "нажать": "PressKeys", "кликнуть": "MouseClick",
    "ввести": "InputBox", "заполнить": "InputBox",
    "записать": "FileWrite", "прочитать": "FileRead", "считать": "FileRead",
    "загрузить": "LoadDataTable", "ждать": "Delay",
    "извлечь": "CollectDataInternet", "выполнить": "InvokeExpression",
    "найти": "FindTextBetween", "снять": "MakeScreenshot", "сделать": "MakeScreenshot",
    "скачать": "DownloadFile", "разбить": "SplitString", "объединить": "JoinString",
}
# Домен из текста → блоки семейства (для доменных роботов: Max, Telegram, 1С, Excel…)
DOMAIN_BLOCKS = {
    "max": ["MaxConnect", "MaxWaitForMessage", "MaxSendMessageToChat", "MaxDownloadFile", "MaxDisconnect"],
    "телеграм": ["TelegramBotCreate", "TelegramBotReceived", "TelegramBotSendText",
                 "TelegramBotDownloadFile", "TelegramBotClose"],
    "telegram": ["TelegramBotCreate", "TelegramBotReceived", "TelegramBotSendText",
                 "TelegramBotDownloadFile", "TelegramBotClose"],
    "excel": ["LoadDataTable", "SetCellValue", "CreateExcelDocument"],
    "эксель": ["LoadDataTable", "SetCellValue", "CreateExcelDocument"],
    "1с": ["1CCreateConnection", "1CExecuteCode", "1CCloseConnection"],
    "1c": ["1CCreateConnection", "1CExecuteCode", "1CCloseConnection"],
    "word": ["CreateWordDocument", "AddTextToParagraphWordDocument"],
    "pdf": ["PDFExtractTables"],
    "почт": ["SendMailMessage"],
    "письм": ["SendMailMessage"],
    "оркестр": ["OrchGetSettingsProject", "OrchCreateTask"],
    "orch": ["OrchGetSettingsProject", "OrchCreateTask"],
    "командн": ["LaunchApp", "PressKeys", "CloseApp"],
    "браузер": ["LaunchBrowser", "BrowserNavigate"],
    "сайт": ["BrowserNavigate", "CollectDataInternet"],
    "файл": ["CopyItem"],
}

VERB_RE = re.compile(r"\b(" + "|".join(sorted(VERB_BLOCKS, key=len, reverse=True)) + r")\b")
LOOP_RE = re.compile(r"\b(\d+)\s*(?:раз|раза|штук[аи]?|снимк[аов]*|скриншот[аов]*)\b", re.IGNORECASE)
FOREACH_RE = re.compile(r"(для кажд|по кажд|каждой строк|каждый элемент|всех строк)", re.IGNORECASE)
WHILE_RE = re.compile(r"\b(пока|до тех пор)\b", re.IGNORECASE)
IF_RE = re.compile(r"\b(если|в случае|при условии|когда)\b", re.IGNORECASE)
AFTER_RE = re.compile(r"^\s*(в конце|после этого|затем|далее|итог)", re.IGNORECASE)

TRUE = {"kind": "boolean_literal", "value": True}


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s*(?:;|\bзатем\b|\bпосле чего\b|\bи потом\b|\bдалее\b)\s*", ". ", text, flags=re.IGNORECASE)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.replace("\r", " "))
    return [p.strip(" -•\t") for p in parts if p.strip(" -•\t")]


def _action_step(sid: str, sentence: str) -> dict | None:
    match = VERB_RE.search(sentence.lower())
    if not match:
        return None
    return {"id": sid, "quote": sentence[:200], "type": "action",
            "block": VERB_BLOCKS[match.group(1)]}


def build_plan(text: str, name: str = "Baseline") -> dict:
    """Правила: N раз → цикл, «если» → условие, «для каждого» → обход строк."""
    variables: list[dict] = []
    steps: list[dict] = []
    counter = 0

    def next_id(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}{counter}"

    def ensure(name_: str, vtype: str) -> None:
        if any(v["name"] == name_ for v in variables):
            return
        if vtype == "Number":
            initial: object = 0
        elif vtype == "DataTable":
            # пустая DataTable: initial=None компилируется в пустой контейнер;
            # строка "" для DataTable — ошибка PLAN-010 в plan_to_ir.
            initial = None
        else:
            initial = ""
        variables.append({"name": name_, "sherpa_type": vtype, "initial": initial})

    loop: dict | None = None
    for sentence in _sentences(text):
        lower = sentence.lower()

        if loop is not None and AFTER_RE.match(sentence):
            steps.append(loop)
            loop = None

        count = LOOP_RE.search(lower)
        if count and loop is None:
            ensure("Индекс", "Number")
            loop = {"id": next_id("loop"), "quote": sentence[:200], "type": "repeat", "kind": "for",
                    "start": 1, "end": int(count.group(1)), "index": "Индекс", "body": []}

        elif FOREACH_RE.search(lower) and loop is None:
            ensure("Таблица", "DataTable")
            ensure("Строка", "Object")
            loop = {"id": next_id("loop"), "quote": sentence[:200], "type": "repeat",
                    "kind": "foreach_rows", "table": "Таблица", "row": "Строка", "body": []}

        elif WHILE_RE.search(lower) and loop is None:
            loop = {"id": next_id("loop"), "quote": sentence[:200], "type": "repeat",
                    "kind": "while", "expr": TRUE, "body": []}

        action = _action_step(next_id("s"), sentence)
        if action is None:
            continue

        extra: list[dict] = []
        for keyword, blocks in DOMAIN_BLOCKS.items():
            if keyword in lower and action["block"] not in blocks:
                extra.append({"id": next_id("s"), "quote": sentence[:200],
                              "type": "action", "block": blocks[0]})
                break

        if IF_RE.search(lower):
            step = {"id": next_id("c"), "quote": sentence[:200], "type": "condition",
                    "expr": TRUE, "then": [action] + extra}
        else:
            step = action
            if extra:
                step = {"id": next_id("g"), "quote": sentence[:200], "type": "action",
                        "block": action["block"], "props": {}}
                extra.insert(0, step)
                step = extra

        target = loop["body"] if loop is not None else steps
        if isinstance(step, list):       # доменные шаги добавляются группой
            target.extend(step)
        else:
            target.append(step)

    if loop is not None:
        steps.append(loop)

    if not steps:
        steps = [{"id": "log1", "quote": "описание не распознано", "type": "action",
                  "block": "Log", "props": {"Value": '"нет шагов"'}}]
    return {"process": {"name": name}, "variables": variables, "steps": steps}


def description_from_process(path: Path, min_len: int = 40) -> str:
    root = ET.parse(path).getroot()
    texts: list[str] = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "DesignerItem":
            continue
        content = item.findtext("Content") or ""
        try:
            inner = ET.fromstring(content)
        except ET.ParseError:
            continue
        if inner.get("InternalName") == "Comment":
            text = (inner.get("DisplayName") or "").strip()
            if len(text) >= min_len:
                texts.append(text)
    root_desc = (root.findtext("Description") or "").strip()
    if root_desc:
        texts.append(root_desc)
    return "\n".join(texts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Baseline: описание → план (без LLM)")
    ap.add_argument("--from-process", type=Path, default=None)
    ap.add_argument("--text", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--print", action="store_true", dest="show")
    args = ap.parse_args(argv)

    if args.from_process:
        text = description_from_process(args.from_process)
        name = args.from_process.stem
    elif args.text:
        text = args.text.read_text(encoding="utf-8-sig")
        name = args.text.stem
    else:
        print("FAIL: нужен --from-process или --text")
        return 2

    if not text.strip():
        print("FAIL: описание не найдено")
        return 2

    plan = build_plan(text, name)
    if args.show:
        print(json.dumps(plan, ensure_ascii=False, indent=2)[:1500])
    if args.out:
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK: {args.out} — шагов {len(plan['steps'])}, переменных {len(plan['variables'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
