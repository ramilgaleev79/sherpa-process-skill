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
    """Уровень узла = длина самого длинного пути от Start (Start = 0).

    Циклы в графе штатны (тело → заголовок цикла). Наивный Kahn на них
    останавливается и оставляет все узлы на уровне 0. Поэтому сначала DFS
    помечает back-edge, затем уровни считаются по DAG без этих рёбер.
    """
    by_key = {n["key"]: n for n in nodes}
    adj: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for idx, e in enumerate(edges):
        adj[e["from"]].append((idx, e))

    back_edges: set[int] = set()
    color: dict[str, int] = {}  # 1 — серый, 2 — чёрный

    def dfs(start: str) -> None:
        stack = [(start, 0)]
        color[start] = 1
        while stack:
            cur, pos = stack[-1]
            if pos >= len(adj[cur]):
                color[cur] = 2
                stack.pop()
                continue
            stack[-1] = (cur, pos + 1)
            idx, e = adj[cur][pos]
            nxt = e["to"]
            if nxt not in by_key:
                continue
            state = color.get(nxt, 0)
            if state == 1:
                back_edges.add(idx)
            elif state == 0:
                color[nxt] = 1
                stack.append((nxt, 0))

    for n in nodes:
        if color.get(n["key"], 0) == 0:
            dfs(n["key"])

    indeg: dict[str, int] = {n["key"]: 0 for n in nodes}
    for idx, e in enumerate(edges):
        if idx not in back_edges and e["to"] in indeg:
            indeg[e["to"]] += 1
    level = {n["key"]: 0 for n in nodes}
    queue = deque(n["key"] for n in nodes if indeg[n["key"]] == 0)
    while queue:
        cur = queue.popleft()
        for idx, e in adj[cur]:
            if idx in back_edges:
                continue
            nxt = e["to"]
            if nxt not in level:
                continue
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
