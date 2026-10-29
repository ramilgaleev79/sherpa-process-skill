"""Срез структуры чужого процесса → лёгкий эталон для eval и ревью.

Большой реальный процесс (215 блоков) как эталон структуры бесполезен: план на
10 шагов не может воспроизвести 200 блоков обработки ошибок, и оценка близости
падает до шума. Но у такого процесса есть **супервизор** — верхний уровень, где
и лежит алгоритм: `Старт → Лог → проверка ошибки → инициализация/работа/конец`.
Срез верхнего уровня — это и есть воспроизводимый эталон для eval-пары.

Глубина считается по контейнерам (та же модель, что в `structure_audit.py`):
`--depth 0` — блоки вне контейнеров, `--depth 1` — плюс тела контейнеров и т.д.

    python scripts/dev/extract_skeleton.py OCR/main.process --depth 0 \\
        --out tests/fixtures/evals/ocr-skeleton/reference.json

Пишется профиль графа (`graph_compare.graph_profile`): типы блоков и связи
`тип:порт → тип:порт` — несколько килобайт вместо мегабайт.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev.graph_compare import graph_profile  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402
from structure_audit import load, model  # noqa: E402


def skeleton(path: Path, depth: int) -> dict:
    """Граф блоков не глубже `depth` контейнеров (0 — только верхний уровень).

    Сам контейнер остаётся в срезе (его порты — часть структуры верхнего уровня),
    выбрасывается только его тело: связи внутрь тела отсекаются, потому что
    целевых блоков в срезе нет.
    """
    blocks, edges = load(path)
    m = model(blocks, edges)
    name = lambda b: blocks[b]["internal"] or "?"
    containers = set(m["containers"])

    def depth_of(bid: str) -> int:
        return sum(1 for c in containers if bid in m["body"].get(c, ()))

    keep = {b for b in m["reach"] if depth_of(b) <= depth}
    kept_edges = {(name(s), sp, name(t), tp)
                  for s, sp, t, tp in edges if s in keep and t in keep}
    graph = {
        "types": Counter(name(b) for b in keep),
        "edges": kept_edges,
        "comments": [],
        "source": f"{path.name} (depth={depth})",
    }
    profile = graph_profile(graph, f"{path.name} (верхний уровень, depth={depth})")
    profile["stats"]["kept_blocks"] = len(keep)
    profile["stats"]["all_blocks"] = len(blocks)
    return profile


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Срез верхнего уровня процесса → профиль графа")
    ap.add_argument("file", type=Path)
    ap.add_argument("--depth", type=int, default=0, help="сколько уровней контейнеров включать")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    profile = skeleton(args.file, args.depth)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stats = profile["stats"]
    print(f"OK: {args.out} — блоков {stats['kept_blocks']} из {stats['all_blocks']}, "
          f"связей {stats['edges']}")
    print("типы: " + ", ".join(f"{k}×{v}" for k, v in sorted(profile["types"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
