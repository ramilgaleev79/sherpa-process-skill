#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замер радиуса планируемой правки .process — БЕЗ записи в файл.

Правка описывается спецификацией (JSON), применяется к модели графа в памяти,
и инструмент показывает, что изменится во всём сценарии:

  * структура: недостижимые/новые блоки, блоки, сменившие контейнер (стек),
    новые GOTO-стрелки, обрывы потока, изменившиеся хабы;
  * данные: переменные, которые правка трогает (пишет/перестаёт писать),
    и сколько мест их читают — «правка флага = правка всех его читателей»;
  * вердикт: локальна правка или расползается на весь сценарий.

Использование:
    python change_impact.py FILE.process --spec spec.json [--json]

Спецификация (все поля необязательны):
{
  "add_blocks":    [{"id": "<guid>", "internal": "SendMailMessage", "display": "…", "props": {}}],
  "remove_blocks": ["<guid>", …],
  "add_edges":     [["<src>", "Next", "<dst>", "Input"]],
  "remove_edges":  [["<src>", "Next", "<dst>", "Input"]],       // null = любой
  "set_props":     [{"block": "<guid>", "name": "FileName", "value": "…"}]  // value=null — удалить
}

Код возврата: 0 — правка локальна, 1 — есть побочные эффекты (см. отчёт).
Только stdlib; модель графа — из structure_audit.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from structure_audit import hot_vars, load, model

MAX_LIST = 12


def apply_spec(blocks: dict, edges: list, spec: dict) -> tuple[dict, list, dict]:
    b = {k: {**v, "props": dict(v["props"])} for k, v in blocks.items()}
    e = list(edges)
    touched_blocks: set[str] = set()

    for x in spec.get("remove_edges", []):
        src, port, dst, sink = (list(x) + [None, None, None, None])[:4]
        e = [t for t in e if not (t[0] == src and (port is None or t[1] == port)
                                  and t[2] == dst and (sink is None or t[3] == sink))]
        touched_blocks.update([src, dst])

    for x in spec.get("add_edges", []):
        src, port, dst, sink = list(x)[:4]
        e.append((src, port, dst, sink))
        touched_blocks.update([src, dst])

    for bid in spec.get("remove_blocks", []):
        b.pop(bid, None)
        e = [t for t in e if t[0] != bid and t[2] != bid]
        touched_blocks.add(bid)

    for nb in spec.get("add_blocks", []):
        b[nb["id"]] = {
            "id": nb["id"], "internal": nb.get("internal"),
            "display": nb.get("display", ""), "enabled": True,
            "props": dict(nb.get("props") or {}),
        }
        touched_blocks.add(nb["id"])

    for sp in spec.get("set_props", []):
        blk = b.get(sp["block"])
        if blk is None:
            print(f"[warn] set_props: нет блока {sp['block'][:8]}", file=sys.stderr)
            continue
        if sp.get("value") is None:
            blk["props"].pop(sp["name"], None)
        else:
            blk["props"][sp["name"]] = sp["value"]
        touched_blocks.add(sp["block"])

    return b, e, {"blocks": touched_blocks}


def short_list(ids, fmt=lambda x: x[:8]) -> str:
    ids = sorted(ids)
    head = ", ".join(fmt(i) for i in ids[:MAX_LIST])
    return head + (f" … ещё {len(ids) - MAX_LIST}" if len(ids) > MAX_LIST else "")


def impact(path: Path, spec: dict) -> dict:
    blocks, edges = load(path)
    before = model(blocks, edges)
    nb, ne, touched = apply_spec(blocks, edges, spec)
    after = model(nb, ne)

    gone = before["reach"] - after["reach"]
    new = after["reach"] - before["reach"]
    common = before["reach"] & after["reach"]

    owner_changed = {x: (before["owners"].get(x), after["owners"].get(x))
                     for x in common if before["owners"].get(x) != after["owners"].get(x)}

    kind_changed = []
    for key in set(before["kind_of"]) & set(after["kind_of"]):
        if before["kind_of"][key] != after["kind_of"][key]:
            kind_changed.append((key, before["kind_of"][key], after["kind_of"][key]))

    new_goto = []
    for key, kind in after["kind_of"].items():
        if not kind.startswith("goto"):
            continue
        if before["kind_of"].get(key) == kind:
            continue
        new_goto.append((key, kind, before["kind_of"].get(key, "новая стрелка")))

    hub_changed = {}
    for b in set(before["hubs"]) | set(after["hubs"]):
        hb, ha = before["hubs"].get(b, set()), after["hubs"].get(b, set())
        if hb != ha:
            hub_changed[b] = (hb, ha)

    dangling = [b for b in common
                if before["out_deg"].get(b, 0) > 0 and after["out_deg"].get(b, 0) == 0]
    orphan = [b for b in new if not after["in_deg"].get(b, 0)]

    # данные: переменные, которые пишут затронутые блоки, и смена набора писателей
    data_notes = []
    for var in sorted(set(before["writes"]) | set(after["writes"])):
        wb = before["writes"].get(var, set())
        wa = after["writes"].get(var, set())
        readers = after["reads"].get(var, set()) | before["reads"].get(var, set())
        writers_touched = (wb | wa) & touched["blocks"]
        if wb != wa or writers_touched:
            data_notes.append({
                "var": var,
                "writers_before": len(wb), "writers_after": len(wa),
                "readers": len(readers),
                "readers_untouched": len(readers - touched["blocks"]),
                "writers_touched": sorted(writers_touched),
                "changed": wb != wa,
            })
    data_notes.sort(key=lambda r: (not r["changed"], -r["readers"]))

    signals = []
    if gone:
        signals.append(f"недостижимых блоков: {len(gone)}")
    if owner_changed:
        signals.append(f"блоков со сменой контейнера (стека): {len(owner_changed)}")
    if new_goto:
        signals.append(f"новых/ухудшившихся GOTO-стрелок: {len(new_goto)}")
    if dangling:
        signals.append(f"обрывов потока (блок теряет выход): {len(dangling)}")
    if hub_changed:
        signals.append(f"хабов с изменённым набором контекстов: {len(hub_changed)}")
    if orphan:
        signals.append(f"новых блоков без входа: {len(orphan)}")
    flag_notes = [d for d in data_notes if d["changed"] and d["readers_untouched"]]
    if flag_notes:
        signals.append(f"переменных со сменой писателей при читателях вне правки: {len(flag_notes)}")

    return {
        "file": path.name,
        "gone": sorted(gone), "new_reachable": sorted(new),
        "owner_changed": owner_changed,
        "kind_changed": kind_changed,
        "new_goto": new_goto,
        "hub_changed": hub_changed,
        "dangling": sorted(dangling), "orphan": sorted(orphan),
        "data": data_notes,
        "hot_before": hot_vars(before), "hot_after": hot_vars(after),
        "touched": sorted(touched["blocks"]),
        "signals": signals,
        "local": not signals,
        "blocks": {"before": len(blocks), "after": len(nb)},
        "edges": {"before": len(edges), "after": len(ne)},
        "meta": {"blocks": blocks, "nb": nb},
    }


def report(res: dict) -> None:
    blocks = res["meta"]["blocks"]
    nb = res["meta"]["nb"]
    label = lambda bid: (f"{bid[:8]} {(nb.get(bid) or blocks.get(bid) or {}).get('internal', '?')}")

    print("=" * 100)
    print(f"{res['file']}: блоков {res['blocks']['before']} → {res['blocks']['after']}, "
          f"стрелок {res['edges']['before']} → {res['edges']['after']}")
    print(f"затронуто правкой блоков: {len(res['touched'])}")

    print("\n1. СТРУКТУРНЫЙ РАДИУС")
    if res["gone"]:
        print(f"   стали недостижимыми ({len(res['gone'])}): {short_list(res['gone'], label)}")
    if res["new_reachable"]:
        print(f"   стали достижимыми ({len(res['new_reachable'])}): {short_list(res['new_reachable'], label)}")
    if res["owner_changed"]:
        print(f"   сменили контейнер — исполнятся в другом стеке ({len(res['owner_changed'])}):")
        for b, (a, c) in sorted(res["owner_changed"].items())[:MAX_LIST]:
            print(f"      {label(b):42s} {a[:8] if a else 'верхний уровень':8s} → "
                  f"{c[:8] if c else 'верхний уровень'}")
        if len(res["owner_changed"]) > MAX_LIST:
            print(f"      … ещё {len(res['owner_changed']) - MAX_LIST}")
    if res["new_goto"]:
        print(f"   GOTO: новых/ухудшившихся {len(res['new_goto'])}")
        for (s, sp, t, _tp), kind, was in res["new_goto"]:
            print(f"      {label(s):42s} --{sp:6s}--> {label(t):42s} {was} → {kind}")
    if res["dangling"]:
        print(f"   обрыв потока ({len(res['dangling'])}): {short_list(res['dangling'], label)}")
    if res["hub_changed"]:
        print(f"   хабы изменили контексты ({len(res['hub_changed'])}):")
        for b, (hb, ha) in sorted(res["hub_changed"].items())[:MAX_LIST]:
            print(f"      {label(b):42s} было {len(hb)} контекст(ов) → стало {len(ha)}")
    if res["orphan"]:
        print(f"   новые блоки без входа ({len(res['orphan'])}): {short_list(res['orphan'], label)}")

    print("\n2. РАДИУС ДАННЫХ (флаги и переменные, которых правка касается)")
    if not res["data"]:
        print("   переменные не затронуты")
    for d in res["data"][:MAX_LIST]:
        arrow = f"{d['writers_before']}→{d['writers_after']}" if d["changed"] else f"{d['writers_after']}"
        note = ""
        if not d["changed"] and d["writers_touched"]:
            note = f"  ← правка его не трогает, но {d['writers_touched'][0][:8]} продолжает писать"
        print(f"   ${d['var']:24s} писателей {arrow:8s} читают {d['readers']:3d} "
              f"(вне правки {d['readers_untouched']:3d}){note}")
    if len(res["data"]) > MAX_LIST:
        print(f"   … ещё {len(res['data']) - MAX_LIST}")

    print("\n3. ВЕРДИКТ")
    if res["local"]:
        print("   правка локальна: структурных и связных побочных эффектов нет")
    else:
        print("   ПРАВКА РАСПОЛЗАЕТСЯ:")
        for s in res["signals"]:
            print(f"     - {s}")


def _jsonable(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"не сериализуется: {type(o)!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Радиус планируемой правки .process (без записи в файл)")
    ap.add_argument("file")
    ap.add_argument("--spec", required=True, help="JSON со спецификацией правки")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    res = impact(path, spec)
    if args.json:
        payload = {k: v for k, v in res.items() if k != "meta"}
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=_jsonable))
    else:
        report(res)
    return 0 if res["local"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
