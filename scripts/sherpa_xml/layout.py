"""Детерминированная раскладка блоков (main.md §8).

Упрощённая, но стабильная политика: вертикальные дорожки по уровню
максимального пути от Start (Top растёт с глубиной — как в golden-цепочках),
позиция в дорожке — по порядку ключей узлов в IR.

Параметры соответствуют §8: x_strategy=stable_incremental, y_strategy=lane_based,
шаги 240×140. Layout — не бизнес-логика; достаточно детерминизма.
"""

from __future__ import annotations

from collections import defaultdict, deque

START_X = 700
START_Y = 635
SPACING_X = 240
SPACING_Y = 140


def _longest_path_levels(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """Уровень узла = длина самого длинного пути от Start (Start = 0)."""
    incoming: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {n["key"]: 0 for n in nodes}
    for e in edges:
        incoming[e["to"]].append(e["from"])
        indeg[e["to"]] += 1
    level = {n["key"]: 0 for n in nodes}
    queue = deque(n["key"] for n in nodes if indeg[n["key"]] == 0)
    # Kahn; для циклов (While→body→While) уровень фиксируется по первому проходу —
    # детерминированно, т.к. порядок очереди задан порядком узлов в IR.
    while queue:
        cur = queue.popleft()
        for e in edges:
            if e["from"] != cur:
                continue
            nxt = e["to"]
            level[nxt] = max(level[nxt], level[cur] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return level


def compute_layout(nodes: list[dict], edges: list[dict]) -> dict[str, tuple[int, int]]:
    """Возвращает {node_key: (left, top)} — детерминированно для одного IR."""
    level = _longest_path_levels(nodes, edges)
    by_level: dict[int, list[str]] = defaultdict(list)
    for n in nodes:  # порядок IR — норматив
        by_level[level[n["key"]]].append(n["key"])
    layout: dict[str, tuple[int, int]] = {}
    for depth in sorted(by_level):
        for lane, key in enumerate(by_level[depth]):
            layout[key] = (START_X + lane * SPACING_X, START_Y + depth * SPACING_Y)
    return layout
