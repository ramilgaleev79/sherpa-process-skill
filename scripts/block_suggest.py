#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подсказка блока по глаголу из задачи — корпусные кандидаты вместо grep по 752 блокам.

Шаг 3 протокола синтеза (`references/graph-synthesis.md`): «блоки под каждую
строку таблицы — по глаголу из задачи». Раньше это был поиск глазами по каталогу
или grep, теперь — корпусный индекс `assets/verb_block_index.json`: он собран
`scripts/dev/build_verb_index.py` из реальных процессов по `DisplayName` и знает
частоты («отправить» → `SendMailMessage` ×28, `ReSendMailMessage` ×4 …).

Инструмент ничего не выбирает за модель: он показывает кандидатов с частотами и
помечает блоки вне каталога 1.4.4. Решение остаётся за каталогом
(`assets/block_index.json`) и текстом задачи.

Использование:
    python scripts/block_suggest.py "отправить письмо"
    python scripts/block_suggest.py "найти файлы" --limit 8 --json

Код возврата: 0 — есть кандидаты, 1 — ничего не найдено (тогда уместен ToDo).
Только stdlib.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sherpa_xml.console import utf8_stdio  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
INDEX = SKILL_ROOT / "assets" / "verb_block_index.json"
CATALOG = SKILL_ROOT / "assets" / "block_index.json"
MIN_PREFIX = 4          # «отправка» и «отправить» совпадают по общему префиксу
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_-]+")

# Приставки, которые не меняют выбор блока: «скопировать» и «копировать» — один
# глагол, «отправить» и «править» — разные. Поэтому снимаем приставку только
# у длинных основ и сравниваем уже нормализованные формы.
PREFIXES = ("пере", "пред", "недо", "раз", "рас", "воз", "вос", "вы", "за", "на",
            "от", "до", "при", "про", "под", "над", "об", "по", "с", "у", "из", "ис")
MIN_STEM = 5
SKIP_BLOCKS = {"ToDoBlock", "Comment", "Start", "ReturnOrStop", "PointConnector"}


def _normalize(word: str) -> str:
    """Грубая нормализация глагола: снять приставку от длинной основы."""
    low = word.lower()
    for prefix in PREFIXES:
        if low.startswith(prefix) and len(low) - len(prefix) >= MIN_STEM:
            return low[len(prefix):]
    return low


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def match_verbs(query: str, verbs: dict) -> list[str]:
    """Глаголы индекса, совпавшие с запросом (нормализация + общий префикс)."""
    words = [_normalize(w) for w in WORD_RE.findall(query.lower())]
    matched = []
    for verb in verbs:
        norm = _normalize(verb)
        for word in words:
            if not word:
                continue
            if word == norm:
                matched.append(verb)
                break
            short, long_ = sorted((word, norm), key=len)
            if len(short) >= MIN_PREFIX and long_.startswith(short):
                matched.append(verb)
                break
    return matched


def _catalog_candidates(query: str, catalog: dict, limit: int) -> list[dict]:
    """Запасной путь: поиск по подписи/названию блока в каталоге."""
    needle = query.lower().strip()
    words = [w.lower() for w in WORD_RE.findall(needle)]
    rows = []
    for internal, entry in catalog.items():
        haystack = " ".join(str(entry.get(k) or "") for k in
                            ("display_name", "friendly_name", "group")).lower()
        score = 0
        if needle and needle in haystack:
            score = 3
        elif words and all(w in haystack for w in words):
            score = 2
        elif any(w in haystack for w in words if len(w) > 3):
            score = 1
        if not score:
            continue
        rows.append({
            "block": internal,
            "display": entry.get("display_name") or "",
            "group": entry.get("group") or "",
            "ports": entry.get("ports") or [],
            "in_palette": bool(entry.get("palette")),
            "count": 0,
            "confidence": 0.0,
            "_score": score,
        })
    rows.sort(key=lambda r: (-r["_score"], not r["in_palette"], r["block"]))
    return rows[:limit]


def suggest(query: str, limit: int = 5) -> dict:
    index = _load(INDEX)
    catalog = _load(CATALOG).get("blocks") or {}
    verbs_index = index.get("verbs") or {}
    matched = match_verbs(query, verbs_index)

    scores: dict[str, dict] = {}
    for verb in matched:
        info = verbs_index[verb]
        for block, count in info["blocks"].items():
            if block in SKIP_BLOCKS:      # заготовки и служебные блоки — не шаги алгоритма
                continue
            row = scores.setdefault(block, {"count": 0, "confidence": 0.0, "verbs": []})
            row["count"] += count
            row["confidence"] = max(row["confidence"], float(info.get("confidence") or 0.0))
            row["verbs"].append(verb)

    candidates, unknown = [], []
    for block, row in scores.items():
        entry = catalog.get(block) or {}
        item = {
            "block": block,
            "count": row["count"],
            "confidence": round(row["confidence"], 2),
            "verbs": row["verbs"],
            "display": entry.get("display_name") or "",
            "group": entry.get("group") or "",
            "ports": entry.get("ports") or [],
            "in_palette": bool(entry.get("palette")),
            "in_catalog": bool(entry),
        }
        (candidates if entry else unknown).append(item)

    candidates.sort(key=lambda c: (-c["count"], not c["in_palette"], c["block"]))
    unknown.sort(key=lambda c: (-c["count"], c["block"]))
    fallback = None
    if not candidates:
        candidates = _catalog_candidates(query, catalog, limit)
        fallback = "catalog" if candidates else None
        for row in candidates:
            row.pop("_score", None)

    return {
        "query": query,
        "verbs": matched,
        "candidates": candidates[:limit],
        "unknown_blocks": unknown[:limit],
        "fallback": fallback,
        "index": index.get("meta") or {},
    }


def report(result: dict, limit: int) -> None:
    print(f"запрос: «{result['query']}»")
    meta = result["index"]
    if meta:
        print(f"корпусный индекс: процессов {meta.get('processes')}, блоков {meta.get('blocks')}, "
              f"глаголов {meta.get('verbs')}")
    if result["verbs"]:
        print("глаголы в индексе: " + ", ".join(result["verbs"]))
    if result["fallback"] == "catalog":
        print("корпусных кандидатов нет — поиск по подписи блока в каталоге:")
    for row in result["candidates"]:
        ports = ", ".join(row["ports"]) or "—"
        palette = "" if row["in_palette"] else "  [нет в палитре]"
        count = f"×{row['count']}" if row["count"] else ""
        print(f"  {row['block']:28s} {count:5s} conf {row['confidence']:.2f}  "
              f"«{row['display']}»  [{row['group']}]  порты: {ports}{palette}")
    if result["unknown_blocks"]:
        print("\nв корпусе есть, но в каталоге 1.4.4 нет (не выбирать):")
        for row in result["unknown_blocks"]:
            print(f"  {row['block']:28s} ×{row['count']} — блок удалён/переименован")
    if not result["candidates"]:
        print("ничего не найдено: по правилу 2 ставьте ToDo с названием действия "
              "(не «похожий» блок)")
    else:
        print("\nрешение принимает каталог и текст задачи: подходит ли действие строки "
              "таблицы; иначе — ToDo.")


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Кандидаты блоков по глаголу из задачи")
    ap.add_argument("query", nargs="+", help="фраза задачи или глагол: «отправить письмо»")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    query = " ".join(args.query)
    if not INDEX.exists():
        print(f"нет индекса {INDEX}: соберите scripts/dev/build_verb_index.py", file=sys.stderr)
        return 2
    result = suggest(query, args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        report(result, args.limit)
    return 0 if result["candidates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
