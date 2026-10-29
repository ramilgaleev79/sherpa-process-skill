#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поблочное сравнение двух версий .process — доказательство «что изменилось, а что нет».

Правка живого сценария делается точечными вставками, поэтому после неё нужно
предъявить не «я поменял только это», а список фактических отличий:

  * новые / удалённые блоки и связи (с учётом кратности);
  * у изменённых блоков — какие свойства и атрибуты поменялись (было → стало);
  * переменные `DiagramProperty` и корневые секции (Description, PSCache,
    InputArguments, OutputArguments) — чтобы «файлы идентичны» не было ложным.

Использование:
    python process_diff.py OLD.process NEW.process [--json]
    python process_diff.py OLD.process NEW.process --expect spec.json

`--expect` — доказательство, а не осмотр: дифф сверяется со спецификацией правки
(`scripts/edit_spec.py`, тот же JSON, по которому считался радиус в
`change_impact.py` и писалась запись в `apply_spec.py`). Проверяется и полнота
(всё заявленное есть), и отсутствие лишнего (ничего сверх заявленного).
Единственное допустимое следствие — связи удалённого блока.

Код возврата: 0 — файлы идентичны (без --expect) или дифф совпал со спецификацией
(--expect); 1 — есть отличия / дифф не совпал; 2 — ошибка чтения.
Только stdlib.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from edit_spec import compare_to_spec, mask_value, render_check
from sherpa_xml.console import utf8_stdio

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
    variables = {}
    for p in root.findall(".//DiagramProperty"):
        name = p.get("Name") or ""
        variables[name] = {
            "type": p.get("Type"),
            "default": p.get("DefaultValue"),
            "value": p.get("Value"),
            "global": p.get("IsGlobal"),
        }
    # Секции, которые не являются блоками/связями/переменными, но меняют файл.
    skip = {"DesignerItems", "Connections", "Properties", "Description"}
    sections = {}
    for child in root:
        local = child.tag.rsplit("}", 1)[-1]
        if local in skip:
            continue
        sections[local] = ET.tostring(child, encoding="unicode")
    return {
        "blocks": blocks,
        "edges": edges,
        "variables": variables,
        "description": root.findtext("Description") or "",
        "sections": sections,
    }


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
    oc = Counter(edge_key(e) for e in old["edges"])
    nc = Counter(edge_key(e) for e in new["edges"])
    ov, nv = old.get("variables", {}), new.get("variables", {})
    added_vars = sorted(set(nv) - set(ov))
    removed_vars = sorted(set(ov) - set(nv))
    changed_vars = []
    for name in sorted(set(ov) & set(nv)):
        delta = {k: [ov[name].get(k), nv[name].get(k)]
                 for k in ("type", "default", "value", "global")
                 if ov[name].get(k) != nv[name].get(k)}
        if delta:
            changed_vars.append({"name": name, "deltas": delta})
    os_, ns_ = old.get("sections", {}), new.get("sections", {})
    section_added = sorted(set(ns_) - set(os_))
    section_removed = sorted(set(os_) - set(ns_))
    section_changed = sorted(k for k in set(os_) & set(ns_) if os_[k] != ns_[k])
    description_changed = old.get("description", "") != new.get("description", "")
    return {
        "added_blocks": [{"id": b, "internal": nb[b]["internal"], "display": nb[b]["display"],
                          "props": nb[b]["props"]} for b in added],
        "removed_blocks": [{"id": b, "internal": ob[b]["internal"], "display": ob[b]["display"]} for b in removed],
        "changed_blocks": changed,
        "added_edges": [list(e) for e, n in nc.items() if n > oc.get(e, 0)],
        "removed_edges": [list(e) for e, n in oc.items() if n > nc.get(e, 0)],
        "added_variables": [{"name": v, **nv[v]} for v in added_vars],
        "removed_variables": [{"name": v, **ov[v]} for v in removed_vars],
        "changed_variables": changed_vars,
        "description_changed": description_changed,
        "added_sections": section_added,
        "removed_sections": section_removed,
        "changed_sections": section_changed,
        "identical": not (
            added or removed or changed or oc != nc
            or added_vars or removed_vars or changed_vars
            or description_changed or section_added or section_removed or section_changed
        ),
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
    print(f"  переменных: +{len(d['added_variables'])} / -{len(d['removed_variables'])} / "
          f"изменено {len(d['changed_variables'])}")
    for b in d["added_blocks"]:
        print(f"  + блок {b['id'][:8]} {b['internal']:20s} \"{b['display'][:40]}\"")
    for b in d["removed_blocks"]:
        print(f"  - блок {b['id'][:8]} {b['internal']:20s} \"{b['display'][:40]}\"")
    for c in d["changed_blocks"]:
        print(f"  ~ блок {c['id'][:8]} {c['internal']:20s} \"{c['display'][:40]}\"")
        for x in c["deltas"]:
            print(f"      {x['kind']:5s} {x['name']:22s} "
                  f"{mask_value(x['name'], x['old'])}  ->  {mask_value(x['name'], x['new'])}")
    for e in d["added_edges"]:
        print(f"  + связь {e[0][:8]} --{e[1]}--> {e[2][:8]} ({e[3]})")
    for e in d["removed_edges"]:
        print(f"  - связь {e[0][:8]} --{e[1]}--> {e[2][:8]} ({e[3]})")
    for v in d["added_variables"]:
        print(f"  + переменная {v['name']} {v.get('type')}")
    for v in d["removed_variables"]:
        print(f"  - переменная {v['name']} {v.get('type')}")
    for v in d["changed_variables"]:
        print(f"  ~ переменная {v['name']}")
        for k, (before, after) in v["deltas"].items():
            print(f"      {k:8s} {short(before)}  ->  {short(after)}")
    if d["description_changed"]:
        print("  ~ корневой Description")
    for tag in d["added_sections"]:
        print(f"  + секция {tag}")
    for tag in d["removed_sections"]:
        print(f"  - секция {tag}")
    for tag in d["changed_sections"]:
        print(f"  ~ секция {tag}")
    print("\nИТОГ:", "файлы идентичны" if d["identical"] else "есть отличия (см. выше)")


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Поблочное сравнение двух .process")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--expect", type=Path, default=None,
                    help="JSON спецификации правки: дифф обязан совпасть с ней один в один")
    args = ap.parse_args(argv)
    try:
        old, new = load(Path(args.old)), load(Path(args.new))
    except Exception as exc:  # noqa: BLE001
        print(f"ошибка чтения: {exc}", file=sys.stderr)
        return 2
    d = diff(old, new)
    check = None
    if args.expect is not None:
        try:
            spec = json.loads(args.expect.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"спецификация не читается: {exc}", file=sys.stderr)
            return 2
        check = compare_to_spec(d, spec, old_model=old)
    if args.json:
        payload = dict(d)
        if check is not None:
            payload["expect"] = check
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        report(d, Path(args.old).name, Path(args.new).name)
        if check is not None:
            print()
            print(render_check(check))
    if check is not None:
        return 0 if check["ok"] else 1
    return 0 if d["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
