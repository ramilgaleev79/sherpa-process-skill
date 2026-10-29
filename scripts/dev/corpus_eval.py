"""Стенд: эталонный `.process` → план из его описания → score близости.

Считает распределение score по корпусу и показывает, каких блоков и связей не хватает
правилам. Это измерение **baseline без LLM**: план строит `plan_from_text.py` только по
тексту описания (глаголы + цифры + «если/пока/для каждого»), поэтому оценка — нижняя
граница; разрыв между ней и ручным планом показывает вклад понимания смысла.

Запуск:
  python scripts/dev/corpus_eval.py --corpus "…\\Projects" --out eval.json
  python scripts/dev/corpus_eval.py --corpus "…\\Projects" --min-score 0.4 --show 10
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev.graph_compare import TECH_DEFAULT, compare, graph_from_ir, read_process  # noqa: E402
from dev.plan_from_text import build_plan, description_from_process  # noqa: E402
from dev.plan_to_ir import PlanCompiler, PlanError  # noqa: E402


def evaluate(corpus: Path) -> dict:
    rows: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []
    missing: Counter = Counter()
    extra: Counter = Counter()

    for path in sorted(corpus.rglob("*.process")):
        desc = description_from_process(path)
        if not desc.strip():
            skipped.append(path.name)
            continue
        plan = build_plan(desc, path.stem)
        try:
            ir_data = PlanCompiler(plan).compile()
        except PlanError as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        ref = read_process(path)
        got = graph_from_ir(ir_data, str(path))
        report = compare(ref, got, set(TECH_DEFAULT))
        missing.update(report["missing_blocks"])
        extra.update(report["extra_blocks"])
        rows.append({
            "file": path.name,
            "score": report["score"],
            "type_score": report["type_score"],
            "edge_score": report["edge_score"],
            "ref_blocks": sum(report["reference"]["blocks"].values()),
            "plan_blocks": sum(report["actual"]["blocks"].values()),
            "missing": report["missing_blocks"][:6],
            "extra": report["extra_blocks"][:6],
            "coverage": round(sum(1 for b in report["reference"]["blocks"] if b in report["actual"]["blocks"])
                              / max(1, len(report["reference"]["blocks"])), 3),
            "legacy": len(ref["legacy"]),
            "identical": report["score"] == 1.0,
        })

    scores = [r["score"] for r in rows]
    buckets = Counter()
    for s in scores:
        if s == 0:
            buckets["0"] += 1
        elif s < 0.2:
            buckets["0–0.2"] += 1
        elif s < 0.4:
            buckets["0.2–0.4"] += 1
        elif s < 0.6:
            buckets["0.4–0.6"] += 1
        elif s < 0.8:
            buckets["0.6–0.8"] += 1
        else:
            buckets["0.8–1.0"] += 1

    return {
        "meta": {"corpus": str(corpus), "with_description": len(rows),
                 "skipped_no_description": len(skipped), "errors": len(errors)},
        "score": {
            "mean": round(statistics.mean(scores), 4) if scores else None,
            "median": round(statistics.median(scores), 4) if scores else None,
            "max": max(scores) if scores else None,
            "buckets": dict(buckets),
        },
        "size": {"mean_ref_blocks": round(statistics.mean([r["ref_blocks"] for r in rows]), 2) if rows else 0,
                 "mean_plan_blocks": round(statistics.mean([r["plan_blocks"] for r in rows]), 2) if rows else 0,
                 "mean_coverage": round(statistics.mean([r["coverage"] for r in rows]), 3) if rows else 0},
        "top_missing_blocks": missing.most_common(20),
        "top_extra_blocks": extra.most_common(15),
        "rows": sorted(rows, key=lambda r: -r["score"]),
        "errors": errors[:20],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Эталон → план из описания → score")
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--show", type=int, default=8, help="сколько процессов показать")
    ap.add_argument("--min-score", type=float, default=None, help="фильтр для показа")
    args = ap.parse_args(argv)

    if not args.corpus.is_dir():
        print(f"FAIL: каталог не найден: {args.corpus}")
        return 2
    report = evaluate(args.corpus)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK: {args.out}")

    meta, score = report["meta"], report["score"]
    print(f"процессов с описанием: {meta['with_description']}  "
          f"без описания: {meta['skipped_no_description']}  ошибок плана: {meta['errors']}")
    if score["mean"] is not None:
        print(f"score: среднее {score['mean']:.3f}  медиана {score['median']:.3f}  максимум {score['max']:.3f}")
        print("распределение:", ", ".join(f"{k}: {v}" for k, v in sorted(score["buckets"].items())))
    print("\nчего не хватает (блоки эталона):")
    for block, n in report["top_missing_blocks"][:12]:
        print(f"  {block:<26} {n}")
    print("\nлишнее (блоки плана):")
    for block, n in report["top_extra_blocks"][:8]:
        print(f"  {block:<26} {n}")

    rows = report["rows"]
    if args.min_score is not None:
        rows = [r for r in rows if r["score"] >= args.min_score]
    print(f"\nлучшие совпадения (топ-{args.show}):")
    for r in rows[:args.show]:
        print(f"  {r['score']:.2f}  {r['file']:<38} эталон {r['ref_blocks']:>2} / план {r['plan_blocks']:>2}"
              f"  нет: {', '.join(r['missing'][:3]) or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
