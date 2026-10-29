"""Запекание корпусного индекса «глагол → блок» в assets/verb_block_index.json.

Зачем отдельный ассет: `corpus_mine.py` умеет извлекать связь «глагол → блок» по
`DisplayName` реальных процессов (сильный слой), но его результат — отчёт для
человека. Шаг 3 протокола синтеза («блоки под каждую строку таблицы») должен
опираться на корпус, а не на grep по 752 блокам, поэтому индекс становится
ассетом скила и читается рантайм-инструментом `scripts/block_suggest.py`.

Пересборка (после обновления корпуса или Designer):

    python scripts/dev/build_verb_index.py            # golden/real + OCR + установка
    python scripts/dev/build_verb_index.py --corpus C:\\corpus --out assets/verb_block_index.json

Индекс детерминирован: ключи отсортированы, пути корпусов в файл не пишутся
(только имена и счётчики) — ассет переносим между машинами.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev.corpus_mine import collect  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402

DEFAULT_OUT = SKILL_ROOT / "assets" / "verb_block_index.json"
APPDATA = Path(os.environ.get("APPDATA", "")) / "Sherpa RPA"


def default_corpora() -> list[Path]:
    """Корпуса по умолчанию: эталоны скила, реальный проект ревью и установка Designer."""
    candidates = [
        SKILL_ROOT / "golden" / "real",
        SKILL_ROOT / "OCR",
        APPDATA / "Designer" / "Library",
        APPDATA / "Frameworks",
        APPDATA / "Projects",
    ]
    return [p for p in candidates if p.is_dir()]


def corpus_name(path: Path) -> str:
    """Имя корпуса без абсолютного пути: ассет не должен зависеть от машины."""
    try:
        return str(path.resolve().relative_to(SKILL_ROOT))
    except ValueError:
        return f"{path.parent.name}/{path.name}"


def build(corpora: list[Path], min_count: int = 1) -> dict:
    report, _described = collect(corpora)
    verbs = {}
    for verb, info in sorted(report["verbs"].items()):
        blocks = {b: n for b, n in info["blocks"].items() if n >= min_count}
        if not blocks:
            continue
        verbs[verb] = {
            "nodes": info["nodes"],
            "documents": info["documents"],
            "confidence": info["confidence"],
            "blocks": dict(sorted(blocks.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
    meta = report["meta"]
    return {
        "meta": {
            "generated_by": "scripts/dev/build_verb_index.py",
            "corpora": [{"name": corpus_name(p), "processes": _count_processes(p)} for p in corpora],
            "processes": meta["processes"],
            "blocks": meta["blocks"],
            "verbs": len(verbs),
            "min_count": min_count,
            "note": ("глагол → блок по DisplayName реальных процессов (сильный слой "
                     "corpus_mine); подсказка, а не автоподстановка"),
        },
        "verbs": verbs,
    }


def _count_processes(path: Path) -> int:
    # ~префикс — автосейв Designer, авторским процессом не считается
    return sum(1 for p in path.rglob("*")
               if p.is_file() and p.name.lower().endswith(".process")
               and not p.name.startswith("~"))


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Корпус → assets/verb_block_index.json")
    ap.add_argument("--corpus", type=Path, nargs="+", default=None,
                    help="каталоги корпуса (по умолчанию: golden/real, OCR, установка Designer)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-count", type=int, default=1)
    args = ap.parse_args(argv)

    corpora = args.corpus or default_corpora()
    missing = [p for p in corpora if not p.is_dir()]
    if missing:
        print(f"FAIL: каталоги не найдены: {', '.join(map(str, missing))}")
        return 2
    if not corpora:
        print("FAIL: нет корпусов — укажите --corpus")
        return 2

    index = build(corpora, args.min_count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
                        encoding="utf-8")
    meta = index["meta"]
    print(f"OK: {args.out}")
    print(f"  корпусов {len(meta['corpora'])}, процессов {meta['processes']}, "
          f"блоков {meta['blocks']}, глаголов {meta['verbs']}")
    for verb, info in list(index["verbs"].items())[:8]:
        top = ", ".join(f"{b}×{n}" for b, n in list(info["blocks"].items())[:3])
        print(f"  «{verb}» → {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
