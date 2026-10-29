#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Аудит структуры .process: где управление держится на GOTO-подобных стрелках.

Считает:
  * классы стрелок — штатные порты контейнеров против прыжков через границы тел;
  * GOTO-назад (цикл стрелкой вместо порта цикла) и GOTO-наружу/вглубь;
  * глубину вложенности и блоки-хабы (входы из разных контекстов);
  * недостижимые блоки, блоки без пути к «Конец»/«Прервать», обрывы потока;
  * def-use переменных (кто пишет, кто читает) — «горячие» флаги вроде $needStop.

Модель: вложенность в .process не хранится (ParentID у всех блоков = 0), поэтому
«блок внутри тела контейнера C» определяем по доминаторам: блок доминируется C.
Стрелка, входящая в такое тело снаружи (не от самого C) или выходящая из него
мимо «Конец»/порта Out, — неструктурная (эквивалент GOTO в обычном языке).

Использование:
    python structure_audit.py FILE.process [FILE.process ...] [--json]

Только stdlib. Функция `model()` используется и в `change_impact.py` — чтобы
«до» и «после» правки считались одним и тем же кодом.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from sherpa_xml.console import utf8_stdio

BODY_PORT = {
    "TryCatchContainer": "Next", "CollapseContainer": "Next",
    "ForeachList": "Item", "ForeachRows": "Row", "For": "Index", "While": "Iteration",
}
EXIT_PORT = {
    "TryCatchContainer": "Out", "CollapseContainer": "Out",
    "ForeachList": "Next", "ForeachRows": "Next", "For": "Next", "While": "Next",
}
LOOPS = {"ForeachList", "ForeachRows", "For", "While"}
STOPS = {"ReturnOrStop", "FullAbort"}

# Свойства-приёмники значений: их значение — запись переменной, а не чтение.
WRITE_PROP = re.compile(
    r"^(Variable\d+|Result[\w]*|Items[\w]*|ResponseContent|ResponseRole|TotalTokens|"
    r"CalledTools|ProcessID|PathUnpack|Row[\w]*|Item[\w]*|Index)$"
)
SKIP_PROP = {"ErrorLevel", "ReportLevel", "ErrorDirection", "SendOrkest", "IsScreenshot"}
# Имена переменных Sherpa — Unicode; кириллица ($Индекс, $Файл) обязательна.
VAR_RE = re.compile(r"\$([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)")
IGNORE_VARS = {"env", "true", "false", "null", "_", "Error", "args", "input", "PSItem", "this"}


def _props(inner: ET.Element) -> tuple[dict[str, str], dict[str, str]]:
    """Значения свойств + CatalogName (Output — это запись переменной)."""
    out: dict[str, str] = {}
    catalog: dict[str, str] = {}
    for child in inner:
        if not child.tag.endswith("ToolboxItemSettings.Properties"):
            continue
        for coll in child:
            for prop in coll:
                name = prop.get("Name") or ""
                out[name] = prop.get("Value") or ""
                catalog[name] = prop.get("CatalogName") or ""
    return out, catalog


def load(path: Path) -> tuple[dict, list]:
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    blocks: dict[str, dict] = {}
    items = root.find("DesignerItems")
    for it in items if items is not None else []:
        inner = ET.fromstring(it.findtext("Content"))
        props, catalog = _props(inner)
        blocks[it.findtext("ID")] = {
            "id": it.findtext("ID"),
            "internal": inner.get("InternalName"),
            "display": (inner.get("DisplayName") or "").replace("\n", " "),
            "enabled": (it.findtext("IsEnabled") or "true").lower() != "false",
            "props": props,
            "prop_catalog": catalog,
        }
    edges = []
    conns = root.find("Connections")
    for c in conns if conns is not None else []:
        edges.append((c.findtext("SourceID"), c.findtext("SourceConnectorName"),
                      c.findtext("SinkID"), c.findtext("SinkConnectorName")))
    return blocks, edges


def dominators(blocks: dict, edges: list):
    pred: dict[str, set] = defaultdict(set)
    succ: dict[str, set] = defaultdict(set)
    for s, _sp, t, _tp in edges:
        if s in blocks and t in blocks:
            pred[t].add(s)
            succ[s].add(t)
    starts = [b for b, m in blocks.items() if m["internal"] == "Start"] or \
             [b for b in blocks if not pred[b]]
    if not starts:
        return {}, set()
    start = starts[0]
    reach, stack = set(), [start]
    while stack:
        b = stack.pop()
        if b in reach:
            continue
        reach.add(b)
        stack.extend(succ[b])
    dom = {n: ({n} if n == start else set(reach)) for n in reach}
    changed = True
    while changed:
        changed = False
        for n in reach:
            if n == start:
                continue
            p = pred[n] & reach
            new = {n} | (set.intersection(*[dom[x] for x in p]) if p else set())
            if new != dom[n]:
                dom[n] = new
                changed = True
    return dom, reach


def defuse(blocks: dict) -> tuple[dict, dict]:
    """Кто пишет переменную и кто её читает (эвристика по свойствам блоков)."""
    writes: dict[str, set] = defaultdict(set)
    reads: dict[str, set] = defaultdict(set)
    for b, meta in blocks.items():
        catalogs = meta.get("prop_catalog", {})
        for name, value in meta["props"].items():
            if not value or name in SKIP_PROP:
                continue
            # CatalogName=Output — штатный write-порт блока (ScreenshotFile, SentEmail,
            # Result, Row, Item…), даже если имя не попало в WRITE_PROP.
            if catalogs.get(name) == "Output" or WRITE_PROP.match(name):
                for v in VAR_RE.findall(value):
                    writes[v].add(b)
            else:
                for v in VAR_RE.findall(value):
                    if v not in IGNORE_VARS:
                        reads[v].add(b)
    return writes, reads


def _reachable_from(start: str | None, edges: list, blocks: dict, stop: str | None = None) -> set[str]:
    """Достижимые блоки от start без захода в stop; только известные ID."""
    if start is None or start not in blocks:
        return set()
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == stop or cur in seen or cur not in blocks:
            continue
        seen.add(cur)
        for s, _sp, t, _tp in edges:
            if s == cur and t not in seen:
                stack.append(t)
    return seen


def model(blocks: dict, edges: list) -> dict:
    """Полная модель графа: владельцы контейнеров, классы стрелок, хабы."""
    dom, reach = dominators(blocks, edges)
    name = lambda b: blocks[b]["internal"] or ""

    containers = [b for b in reach if name(b) in BODY_PORT]

    # Тело контейнера — достижимое от его body-порта МИНУС всё, что достижимо
    # от выходов (Next/Out/Error). Одних доминаторов недостаточно: при одном
    # выходе контейнер доминирует и весь код после него.
    body: dict[str, set[str]] = {}
    for c in containers:
        cname = name(c)
        entry = next((t for s, p, t, _tp in edges if s == c and p == BODY_PORT[cname]), None)
        exits = {
            t for s, p, t, _tp in edges
            if s == c and (p == EXIT_PORT.get(cname) or p == "Error")
        }
        after: set[str] = set()
        for ex in exits:
            after |= _reachable_from(ex, edges, blocks)
        body[c] = _reachable_from(entry, edges, blocks, stop=c) - after - {c}

    # Глубина контейнера = сколько других контейнеров содержат его в своём теле.
    container_depth = {c: sum(1 for d in containers if c in body[d]) for c in containers}

    def owner_of(b: str):
        cands = [c for c in containers if b in body[c]]
        return max(cands, key=lambda c: container_depth[c]) if cands else None

    owners = {b: owner_of(b) for b in reach}

    kinds: dict[str, list] = defaultdict(list)
    kind_of: dict[tuple, str] = {}
    for key in edges:
        s, sp, t, tp = key
        if s not in reach or t not in reach:
            kind = "unreachable"
        else:
            ns, nt = name(s), name(t)
            a, b_ = owners.get(s), owners.get(t)
            if ns in BODY_PORT and sp == BODY_PORT[ns]:
                kind = "body-entry"
            elif ns in BODY_PORT and sp == EXIT_PORT.get(ns):
                kind = "body-exit"
            elif ns in BODY_PORT and sp == "Error":
                kind = "body-error"
            elif nt in LOOPS and t in dom.get(s, set()):
                kind = "loop-back"
            elif t in dom.get(s, set()) and s != t:
                kind = "goto-back"
            elif a != b_:
                if b_ is None:
                    kind = "goto-out"
                elif a is None:
                    kind = "goto-in"
                elif b_ in dom.get(a, set()):
                    kind = "goto-out"
                elif a in dom.get(b_, set()):
                    kind = "goto-in"
                else:
                    kind = "goto-cross"
            else:
                kind = "plain"
        kinds[kind].append(key)
        kind_of[key] = kind

    inbound: dict[str, set] = defaultdict(set)
    out_deg: dict[str, int] = defaultdict(int)
    in_deg: dict[str, int] = defaultdict(int)
    for s, _sp, t, _tp in edges:
        if s in reach and t in reach:
            owner = owners.get(s)
            # возврат из тела в свой же контейнер — не «вход из другого контекста»
            if owner == t:
                continue
            inbound[t].add(owner)
            out_deg[s] += 1
            in_deg[t] += 1
    hubs = {b: {o or "" for o in owners_set} for b, owners_set in inbound.items()
            if len(owners_set) > 1}

    rev: dict[str, set] = defaultdict(set)
    for s, _sp, t, _tp in edges:
        rev[t].add(s)
    can_stop, stack = set(), [b for b in blocks if blocks[b]["internal"] in STOPS]
    while stack:
        cur = stack.pop()
        if cur in can_stop:
            continue
        can_stop.add(cur)
        stack.extend(rev.get(cur, ()))

    depth: dict[int, int] = defaultdict(int)
    for b in reach:
        depth[sum(1 for c in containers if b in body[c])] += 1

    writes, reads = defuse(blocks)

    return {
        "blocks": blocks, "edges": edges, "dom": dom, "reach": reach,
        "containers": containers, "body": body, "owners": owners,
        "kinds": dict(kinds), "kind_of": kind_of, "hubs": hubs,
        "dead": [b for b in blocks if b not in reach],
        "no_stop": [b for b in reach if b not in can_stop and name(b) != "Comment"],
        "depth": dict(sorted(depth.items())),
        "out_deg": dict(out_deg), "in_deg": dict(in_deg),
        "writes": writes, "reads": reads,
    }


def hot_vars(m: dict, limit: int = 8) -> list[dict]:
    """Переменные, правка которых задевает много мест: много читателей/писателей."""
    rows = []
    for var, readers in m["reads"].items():
        writers = m["writes"].get(var, set())
        if len(readers) < 2 and len(writers) < 2:
            continue
        rows.append({"var": var, "writers": len(writers), "readers": len(readers)})
    rows.sort(key=lambda r: (-r["readers"], -r["writers"]))
    return rows[:limit]


def audit(path: Path) -> dict:
    blocks, edges = load(path)
    m = model(blocks, edges)
    name = lambda b: blocks[b]["internal"] or ""
    return {
        "file": path.name,
        "blocks": len(blocks),
        "edges": len(edges),
        "reachable": len(m["reach"]),
        "containers": len(m["containers"]),
        "kinds": {k: len(v) for k, v in m["kinds"].items()},
        "goto": {k: [{"from": s, "port": sp, "to": t, "from_name": name(s), "to_name": name(t),
                      "from_display": blocks[s]["display"], "to_display": blocks[t]["display"]}
                     for s, sp, t, _tp in v]
                 for k, v in m["kinds"].items() if k.startswith("goto")},
        "hubs": [{"block": b, "name": name(b), "display": blocks[b]["display"],
                  "owners": sorted(o for o in owners if o)}
                 for b, owners in m["hubs"].items()],
        "dead": m["dead"],
        "no_stop": m["no_stop"],
        "depth": m["depth"],
        "hot": hot_vars(m),
    }


def report(info: dict) -> None:
    print("=" * 100)
    print(f"{info['file']}: блоков {info['blocks']}, стрелок {info['edges']}, "
          f"достижимо {info['reachable']}, контейнеров {info['containers']}")
    total = max(1, info["edges"])
    print("\nклассы стрелок:")
    for k, v in sorted(info["kinds"].items(), key=lambda x: -x[1]):
        mark = "  <-- неструктурно" if k.startswith("goto") else ""
        print(f"   {k:12s} {v:4d}  ({v * 100 // total:3d}%){mark}")

    for k in ("goto-in", "goto-out", "goto-cross", "goto-back"):
        rows = info["goto"].get(k) or []
        if not rows:
            continue
        title = {"goto-in": "GOTO ВГЛУБЬ ЧУЖОГО ТЕЛА (в обход входа в контейнер)",
                 "goto-out": "GOTO НАРУЖУ ИЗ ТЕЛА (мимо «Конец» и порта Out)",
                 "goto-cross": "GOTO МЕЖДУ РАЗНЫМИ ТЕЛАМИ (разные ветви графа)",
                 "goto-back": "GOTO НАЗАД (цикл стрелкой вместо порта цикла)"}[k]
        print(f"\n{title} ({len(rows)}):")
        for r in rows:
            print(f"   {r['from'][:8]} {r['from_name']:20s} \"{r['from_display'][:22]:22s}\" "
                  f"--{r['port']:6s}--> {r['to'][:8]} {r['to_name']:20s} \"{r['to_display'][:22]:22s}\"")

    if info["hubs"]:
        print(f"\nХАБЫ — входы из разных контекстов ({len(info['hubs'])}):")
        for h in sorted(info["hubs"], key=lambda x: -len(x["owners"])):
            print(f"   {h['block'][:8]} {h['name']:20s} \"{h['display'][:30]:30s}\" из: "
                  + ", ".join(o[:8] for o in h["owners"]))

    if info["hot"]:
        print("\nГОРЯЧИЕ ПЕРЕМЕННЫЕ (правка флага = правка всех его читателей):")
        for h in info["hot"]:
            print(f"   ${h['var']:24s} пишут {h['writers']:3d}, читают {h['readers']:3d}")

    print("\nглубина вложенности (контейнеров над блоком): "
          + ", ".join(f"{d}:{n}" for d, n in info["depth"].items()))
    if info["dead"]:
        print(f"\nнедостижимых блоков: {len(info['dead'])}")
        for b in info["dead"]:
            print(f"   {b[:8]}")
    if info["no_stop"]:
        print(f"\nблоков без пути к «Конец»/«Прервать»: {len(info['no_stop'])}")
        for b in info["no_stop"]:
            print(f"   {b[:8]}")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Аудит структуры .process (GOTO-стрелки, хабы, глубина)")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()
    out = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"нет файла: {p}", file=sys.stderr)
            return 2
        info = audit(p)
        out.append(info)
        if not args.json:
            report(info)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
