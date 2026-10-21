#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поблочное сравнение двух версий .process — доказательство «что изменилось, а что нет».

Правка живого сценария делается точечными вставками, поэтому после неё нужно
предъявить не «я поменял только это», а список фактических отличий:

  * новые / удалённые блоки и связи;
  * у изменённых блоков — какие свойства и атрибуты поменялись (было → стало).

Использование:
    python process_diff.py OLD.process NEW.process [--json]

Код возврата: 0 — файлы идентичны, 1 — есть отличия, 2 — ошибка чтения.
Только stdlib.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ITEM_ATTRS = ("DisplayName", "InternalName", "Version")
ITEM_TAGS = ("Left", "Top", "Width", "Height", "zIndex", "IsEnabled", "Description",
             "IsPSVerified", "ParentID")


def _props(inner: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for child in inner:
        if not child.tag.endswith("ToolboxItemSettings.Properties"):
            continue
        for coll in child:
            for prop in coll:
                out[prop.get("Name") or ""] = prop.get("Value") or ""
    return out


def load(path: Path) -> dict:
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    blocks: dict[str, dict] = {}
    items = root.find("DesignerItems")
    for it in items if items is not None else []:
        inner = ET.fromstring(it.findtext("Content"))
        blocks[it.findtext("ID")] = {
            "internal": inner.get("InternalName"),
            "display": (inner.get("DisplayName") or "").replace("\n", " "),
            "version": inner.get("Version"),
            "attrs": {t: it.findtext(t) for t in ITEM_TAGS},
            "props": _props(inner),
            "raw": ET.tostring(it, encoding="unicode"),
        }
    edges = []
    conns = root.find("Connections")
    for c in conns if conns is not None else []:
        edges.append({k: c.findtext(k) for k in
                      ("SourceID", "SourceConnectorName", "SinkID", "SinkConnectorName")})
    return {"blocks": blocks, "edges": edges}


def edge_key(e: dict) -> tuple:
    return (e["SourceID"], e["SourceConnectorName"], e["SinkID"], e["SinkConnectorName"])


def diff(old: dict, new: dict) -> dict:
    ob, nb = old["blocks"], new["blocks"]
    added = [b for b in nb if b not in ob]
    removed = [b for b in ob if b not in nb]
    changed = []
    for b in nb:
        if b not in ob:
            continue
        o, n = ob[b], nb[b]
        if o["raw"] == n["raw"]:
            continue
        deltas = []
        for k in ITEM_TAGS:
            if o["attrs"].get(k) != n["attrs"].get(k):
                deltas.append({"kind": "tag", "name": k,
                               "old": o["attrs"].get(k), "new": n["attrs"].get(k)})
        if o["display"] != n["display"]:
            deltas.append({"kind": "attr", "name": "DisplayName",
                           "old": o["display"], "new": n["display"]})
        if o["version"] != n["version"]:
            deltas.append({"kind": "attr", "name": "Version",
                           "old": o["version"], "new": n["version"]})
        for p in sorted(set(o["props"]) | set(n["props"])):
            if o["props"].get(p) != n["props"].get(p):
                deltas.append({"kind": "prop", "name": p,
                               "old": o["props"].get(p), "new": n["props"].get(p)})
        if not deltas:
            deltas.append({"kind": "raw", "name": "(прочее)", "old": "…", "new": "…"})
        changed.append({"id": b, "internal": n["internal"], "display": n["display"],
                        "deltas": deltas})
    oe, ne = {edge_key(e) for e in old["edges"]}, {edge_key(e) for e in new["edges"]}
    return {
        "added_blocks": [{"id": b, "internal": nb[b]["internal"], "display": nb[b]["display"]} for b in added],
        "removed_blocks": [{"id": b, "internal": ob[b]["internal"], "display": ob[b]["display"]} for b in removed],
        "changed_blocks": changed,
        "added_edges": [list(e) for e in ne - oe],
        "removed_edges": [list(e) for e in oe - ne],
        "identical": not (added or removed or changed or (ne ^ oe)),
    }


def short(v) -> str:
    if v is None:
        return "—"
    v = str(v).replace("\n", " ")
    return v[:100] + ("…" if len(v) > 100 else "")


def report(d: dict, old_name: str, new_name: str) -> None:
    print(f"{old_name}  ->  {new_name}")
    print(f"  блоков: +{len(d['added_blocks'])} / -{len(d['removed_blocks'])} / "
          f"изменено {len(d['changed_blocks'])}")
    print(f"  связей: +{len(d['added_edges'])} / -{len(d['removed_edges'])}")
    for b in d["added_blocks"]:
        print(f"  + блок {b['id'][:8]} {b['internal']:20s} \"{b['display'][:40]}\"")
    for b in d["removed_blocks"]:
        print(f"  - блок {b['id'][:8]} {b['internal']:20s} \"{b['display'][:40]}\"")
    for c in d["changed_blocks"]:
        print(f"  ~ блок {c['id'][:8]} {c['internal']:20s} \"{c['display'][:40]}\"")
        for x in c["deltas"]:
            print(f"      {x['kind']:5s} {x['name']:22s} {short(x['old'])}  ->  {short(x['new'])}")
    for e in d["added_edges"]:
        print(f"  + связь {e[0][:8]} --{e[1]}--> {e[2][:8]} ({e[3]})")
    for e in d["removed_edges"]:
        print(f"  - связь {e[0][:8]} --{e[1]}--> {e[2][:8]} ({e[3]})")
    print("\nИТОГ:", "файлы идентичны" if d["identical"] else "есть отличия (см. выше)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Поблочное сравнение двух .process")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        old, new = load(Path(args.old)), load(Path(args.new))
    except Exception as exc:  # noqa: BLE001
        print(f"ошибка чтения: {exc}", file=sys.stderr)
        return 2
    d = diff(old, new)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        report(d, Path(args.old).name, Path(args.new).name)
    return 0 if d["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
