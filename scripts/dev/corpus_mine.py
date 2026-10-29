"""Майнинг корпуса «описание + .process» → рекомендации для таблицы шагов.

Описание ищется в двух местах:
1. **внутри `.process`** — `Comment`-блоки (текст в `DisplayName`) и корневой
   `Description`. Именно так устроены корпуса Sherpa: описание задачи лежит в
   комментарии на диаграмме, отдельный файл не нужен;
2. рядом с процессом — `<stem>.md` / `.txt` (если есть, приоритет у него).

Учитываются реальные особенности корпусов:

- **старые версии блоков**: версия из файла сверяется с установленной библиотекой,
  блок помечается `legacy` (в статистике видно, сколько правил выведено по старым
  версиям);
- **блоки, которых больше нет** (`LaunchChrome`, `NewFile`, …) — в `unknown`,
  статистика по ним не строится, но файл не отбрасывается;
- **разрывы связей**: связи на несуществующие ID (`dangling`) не участвуют в
  цепочках, а блоки без связей (`isolated`) не считаются разрывом, если это
  комментарии.

Извлекается:

- **глагол → блок** по `DisplayName` узлов (сильный слой) и по тексту описания (слабый);
- **цепочки** `A → B` по связям;
- **конвенции портов**;
- **частота конструкций** (циклы, условия, контейнеры).

Ограничение: сопоставление «фраза ↔ блок» по описанию — документное; сильный сигнал
даёт только `DisplayName`. Результат — рекомендации с частотой, а не автоподстановка.

Запуск:
  python scripts/dev/corpus_mine.py --corpus "…\\Designer\\Library" "…\\Frameworks" "…\\Projects"
  python scripts/dev/corpus_mine.py --corpus corpus/ --out insights.json --min-count 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev.graph_compare import MIN_COMMENT_LEN, TECH_DEFAULT, load_graph  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402

PROCESS_SUFFIXES = {".process", ".ir.json", ".plan.json"}
DESC_SUFFIXES = {".md", ".txt", ".description"}
LOOPS = {"For", "While", "ForeachRows", "ForeachList", "ForeachHashtable"}

VERBS = [
    "сделать", "создать", "создавать", "открыть", "открывать", "закрыть", "закрывать",
    "отправить", "отправлять", "послать", "получить", "получать",
    "сохранить", "сохранять", "записать", "записывать", "прочитать", "читать", "считать",
    "загрузить", "загружать", "выгрузить", "выгружать",
    "скопировать", "копировать", "переместить", "перемещать", "переименовать",
    "удалить", "удалять", "проверить", "проверять", "найти", "находить",
    "нажать", "нажимать", "кликнуть", "кликать", "ввести", "вводить",
    "заполнить", "заполнять", "выбрать", "выбирать", "ждать", "повторить",
    "распознать", "распознавать", "сравнить", "запустить", "запускать",
    "остановить", "останавливать", "авторизоваться", "скачать", "скачивать",
    "сформировать", "извлечь", "обработать", "обновить", "установить",
    "подключиться", "снять", "разархивировать", "архивировать", "распаковать",
    "выполнить", "вычислять", "вычислить", "измерить", "соединить", "объединить",
    "разбить", "преобразовать", "конвертировать", "переслать", "ответить",
    "опубликовать", "свернуть", "развернуть", "печатать", "напечатать",
    "искать", "разобрать", "распарсить", "закодировать", "декодировать",
]
VERB_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(v) for v in VERBS), key=len, reverse=True)) + r")\b")


def _stem(path: Path) -> str:
    name = path.name
    for suf in (".ir.json", ".plan.json", ".process"):
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return path.stem


def _verbs(text: str) -> set[str]:
    return set(VERB_RE.findall((text or "").lower()))


def collect(corpora: list[Path], min_comment_len: int = MIN_COMMENT_LEN) -> tuple[dict, list[str]]:
    processes: list[Path] = []
    descriptions: dict[tuple[Path, str], Path] = {}
    for corpus in corpora:
        for path in sorted(corpus.rglob("*")):
            if not path.is_file():
                continue
            low = path.name.lower()
            if path.name.startswith("~"):
                continue  # автосейв Designer (~имя) — не авторский контент
            if any(low.endswith(s) for s in PROCESS_SUFFIXES):
                if path not in processes:          # одинаковые имена в разных корпусах не схлопываются
                    processes.append(path)
            elif path.suffix.lower() in DESC_SUFFIXES:
                descriptions.setdefault((path.parent, _stem(path)), path)

    verb_blocks: dict[str, Counter] = defaultdict(Counter)   # сильный слой: DisplayName
    verb_nodes: Counter = Counter()
    verb_docs: Counter = Counter()                            # слабый слой: описание
    chains: Counter = Counter()
    ports: Counter = Counter()
    constructs: Counter = Counter()

    stats: Counter = Counter()
    legacy_detail: Counter = Counter()
    replacements: Counter = Counter()
    per_process: list[dict] = []
    notes: list[str] = []
    described_stems: list[str] = []

    for proc in sorted(processes):
        stem = _stem(proc)
        try:
            graph = load_graph(proc)
        except Exception as exc:  # noqa: BLE001 — битый файл не должен ронять корпус
            notes.append(f"пропущен {proc.name}: {exc}")
            stats["unreadable"] += 1
            continue

        stats["processes"] += 1
        stats["blocks"] += sum(graph["types"].values())
        stats["connections"] += graph.get("connections", len(graph["edges"]))
        if graph["legacy"]:
            stats["legacy_blocks"] += sum(graph["types"][b] for b in graph["legacy"])
            stats["files_with_legacy"] += 1
            for block in graph["legacy"]:
                legacy_detail[block] += graph["types"][block]
        for block, replacement in (graph.get("replacements") or {}).items():
            replacements[(block, replacement)] += graph["types"][block]
        if graph["unknown"]:
            stats["unknown_blocks"] += sum(graph["types"][b] for b in graph["unknown"])
            stats["files_with_unknown"] += 1
        if graph["dangling"]:
            stats["dangling_edges"] += graph["dangling"]
            stats["files_with_dangling"] += 1
        if graph["isolated"]:
            stats["isolated_nodes"] += len(graph["isolated"])
            stats["files_with_isolated"] += 1

        # описание: приоритет — отдельный файл, иначе комментарии внутри .process
        sidecar = descriptions.get((proc.parent, stem))
        comments = [c for c in graph.get("comments", []) if len(c) >= min_comment_len]
        if sidecar is not None:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
            source = "sidecar"
            stats["described_sidecar"] += 1
        elif comments or graph.get("description"):
            text = "\n".join(comments) if comments else graph["description"]
            source = "inline"
            stats["described_inline"] += 1
        else:
            text, source = "", "none"
            stats["undescribed"] += 1
        if text:
            described_stems.append(stem)

        for block, n in graph["types"].items():
            if block in LOOPS:
                constructs["loops"] += n
            elif block == "IfCondition":
                constructs["conditions"] += n
            elif block in {"TryCatchContainer", "CollapseContainer"}:
                constructs["containers"] += n

        for (a, ap, b, bp) in graph["edges"]:
            if a in TECH_DEFAULT or b in TECH_DEFAULT:
                ports[(ap, bp)] += 1
                continue
            chains[(a, b)] += 1
            ports[(ap, bp)] += 1

        # сильный слой: глагол из DisplayName конкретного блока → тип этого блока
        for node_type, display in graph.get("nodes", []):
            if node_type in TECH_DEFAULT:      # Comment/Start/Stop — не шаги алгоритма
                continue
            for verb in _verbs(display):
                verb_blocks[verb][node_type] += 1
                verb_nodes[verb] += 1

        # слабый слой: глаголы из описания (документный уровень)
        for verb in _verbs(text):
            verb_docs[verb] += 1

        per_process.append({
            "stem": stem, "file": proc.name, "blocks": dict(graph["types"]),
            "edges": len(graph["edges"]), "description": source,
            "legacy": len(graph["legacy"]), "unknown": len(graph["unknown"]),
            "dangling": graph["dangling"], "isolated": len(graph["isolated"]),
        })

    verbs_out: dict[str, dict] = {}
    for verb, counter in sorted(verb_blocks.items()):
        top = counter.most_common(5)
        verbs_out[verb] = {
            "nodes": verb_nodes[verb],
            "documents": verb_docs[verb],
            "blocks": dict(top),
            "confidence": round(top[0][1] / verb_nodes[verb], 2) if verb_nodes[verb] else 0.0,
        }

    report = {
        "meta": {
            "processes": stats["processes"],
            "described_inline": stats["described_inline"],
            "described_sidecar": stats["described_sidecar"],
            "undescribed": stats["undescribed"],
            "blocks": stats["blocks"],
            "connections": stats["connections"],
            "legacy_blocks": stats["legacy_blocks"],
            "files_with_legacy": stats["files_with_legacy"],
            "unknown_blocks": stats["unknown_blocks"],
            "files_with_unknown": stats["files_with_unknown"],
            "dangling_edges": stats["dangling_edges"],
            "files_with_dangling": stats["files_with_dangling"],
            "isolated_nodes": stats["isolated_nodes"],
            "unreadable": stats["unreadable"],
            "verbs": len(verbs_out),
        },
        "legacy_top": [[b, n] for b, n in legacy_detail.most_common(10)],
        "replacements": [{"removed": b, "replacement": r, "count": n}
                         for (b, r), n in replacements.most_common()],
        "constructs": dict(constructs),
        "verbs": verbs_out,
        "chains": [[a, b, n] for (a, b), n in chains.most_common(25)],
        "ports": [[a, b, n] for (a, b), n in ports.most_common(15)],
        "processes": per_process[:200],
        "notes": notes,
    }
    return report, described_stems


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Корпус «описание + .process» → рекомендации")
    ap.add_argument("--corpus", type=Path, nargs="+", required=True,
                    help="один или несколько каталогов корпуса")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--min-comment-len", type=int, default=MIN_COMMENT_LEN,
                    help="минимальная длина комментария-описания")
    args = ap.parse_args(argv)

    missing = [c for c in args.corpus if not c.is_dir()]
    if missing:
        print(f"FAIL: каталоги не найдены: {', '.join(map(str, missing))}")
        return 2
    report, described = collect(args.corpus, args.min_comment_len)
    report["chains"] = [c for c in report["chains"] if c[2] >= args.min_count]
    report["ports"] = [p for p in report["ports"] if p[2] >= args.min_count]

    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK: {args.out}")

    meta = report["meta"]
    print(f"процессов: {meta['processes']}  с описанием: {meta['described_inline']} внутри / "
          f"{meta['described_sidecar']} рядом  без описания: {meta['undescribed']}")
    print(f"блоков: {meta['blocks']}  связей: {meta['connections']}  "
          f"изолированных: {meta['isolated_nodes']}  "
          f"старых версий: {meta['legacy_blocks']} ({meta['files_with_legacy']} файлов)  "
          f"неизвестных: {meta['unknown_blocks']}  разрывов: {meta['dangling_edges']} "
          f"({meta['files_with_dangling']} файлов)")
    if report.get("replacements"):
        print("удалённые блоки:", ", ".join(
            f"{r['removed']}×{r['count']} → {r['replacement']}" for r in report["replacements"]))
    if report["constructs"]:
        print("конструкции:", ", ".join(f"{k}={v}" for k, v in sorted(report["constructs"].items())))
    for verb, info in list(report["verbs"].items())[:10]:
        blocks = ", ".join(f"{b}×{n}" for b, n in info["blocks"].items())
        print(f"  «{verb}» → {blocks}  (узлов {info['nodes']}, док. {info['documents']})")
    for a, b, n in report["chains"][:6]:
        print(f"  цепочка {a} → {b} ×{n}")
    for a, b, n in report["ports"][:4]:
        print(f"  порт {a} → {b} ×{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
