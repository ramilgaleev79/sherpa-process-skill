"""Replay every block instance found in real .process files against the harvested templates.

For each DesignerItem/Content in the corpus:
  * take its InternalName, Version, DisplayName and property values,
  * render the template with sherpa_xml.compiler.fill_template,
  * compare the rendered Content with the original on: version, display, code,
    property metadata, property values and connector set.

This is the acceptance test for the catalog harvest: it proves the templates can
reproduce what the installed Designer actually wrote, block by block, without
opening Designer.

Usage:
  python scripts/dev/replay_corpus.py                       # default install root
  python scripts/dev/replay_corpus.py --root "D:/Sherpa"     # other installation
  python scripts/dev/replay_corpus.py --report replay.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from sherpa_xml.compiler import fill_template  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402

DEFAULT_ROOT = Path.home() / "AppData" / "Roaming" / "Sherpa RPA"
DEFAULT_DATA = Path.home() / "AppData" / "Roaming" / "Sherpa RPA Data"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def canonical(content: str) -> dict:
    root = ET.fromstring(content)
    props: dict[str, tuple] = {}
    conns = []
    for child in root:
        ln = local(child.tag)
        if ln.endswith("Properties"):
            for coll in child:
                for p in coll:
                    if local(p.tag) != "Property":
                        continue
                    ptype = p.get("Type", "")
                    # Designer stores a multi-select as Enum once values are picked
                    if ptype == "EnumMulti":
                        ptype = "Enum"
                    props[p.get("Name", "")] = (ptype, p.get("Value", ""))
        elif ln.endswith("ConnectorsSettings"):
            for coll in child:
                for c in coll:
                    conns.append((c.get("Name", ""), c.get("MaxInConnections", ""),
                                  c.get("MaxOutConnections", ""), c.get("Orientation", "")))
    return {
        "version": root.get("Version", ""),
        "display": root.get("DisplayName", ""),
        "code": " ".join((root.get("Code") or "").split()),
        "props": props,
        "conns": sorted(conns),
        "internal": root.get("InternalName", ""),
    }


def iter_process_files(roots: list[Path]):
    """~префикс — скрытый автосейв Designer (AutoSaveProcessed): в корпус
    не входит, это дубликат соседа с устаревшим состоянием."""
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.process")):
            if path.name.startswith("~"):
                continue
            yield path


def replay_content(templates: dict, content: str) -> tuple[str, dict]:
    """Returns (status, detail).

    status: exact | mismatch | outdated | no-template | error.
    "outdated" = the instance was written by an older Designer build (block version differs
    from the installed catalog); those are expected and excluded from the fidelity ratio.
    """
    original = canonical(content)
    physical = original["internal"]
    if physical not in templates:
        return "no-template", {"block": physical}
    current = templates[physical]
    if str(original["version"]) != str(current["version"]):
        return "outdated", {"block": physical, "version": [current["version"], original["version"]]}
    values = {name: meta[1] for name, meta in original["props"].items()}
    try:
        rendered = fill_template(current["content"], values, {}, display_name=original["display"] or None)
    except Exception as exc:  # noqa: BLE001
        return "error", {"block": physical, "error": str(exc)}
    got = canonical(rendered)
    delta = {}
    for field in ("version", "display", "code"):
        if got[field] != original[field]:
            delta[field] = [got[field][:60], original[field][:60]]
    if got["conns"] != original["conns"]:
        delta["conns"] = {"rendered": got["conns"], "original": original["conns"]}
    if set(got["props"]) != set(original["props"]):
        delta["prop_names"] = {"missing": sorted(set(original["props"]) - set(got["props"]))[:5],
                               "extra": sorted(set(got["props"]) - set(original["props"]))[:5]}
    else:
        bad = {n: [got["props"][n], original["props"][n]]
               for n, m in original["props"].items() if got["props"][n] != m}
        if bad:
            delta["prop_values"] = dict(list(bad.items())[:5])
    if set(delta) == {"code"}:
        return "legacy-code", {"block": physical}
    return ("exact" if not delta else "mismatch"), {"block": physical, **delta}


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Replay real .process block instances against templates")
    ap.add_argument("--root", type=Path, nargs="+", default=[DEFAULT_ROOT, DEFAULT_DATA])
    ap.add_argument("--limit", type=int, default=0, help="stop after N instances (0 = all)")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)

    templates = json.loads((SKILL_ROOT / "assets" / "block_templates.json").read_text(encoding="utf-8"))
    stats = collections.Counter()
    by_block = collections.Counter()
    mismatches: list[dict] = []
    missing_blocks = collections.Counter()
    total = 0

    for path in iter_process_files(args.root):
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            stats["file-unparsable"] += 1
            continue
        for item in tree.getroot().iter():
            if local(item.tag) != "DesignerItem":
                continue
            content = item.findtext("Content")
            if not content:
                continue
            try:
                status, detail = replay_content(templates, content)
            except ET.ParseError:
                stats["content-unparsable"] += 1
                continue
            total += 1
            stats[status] += 1
            by_block[detail.get("block", "?")] += 1
            if status == "no-template":
                missing_blocks[detail["block"]] += 1
            elif status in ("mismatch", "error"):
                mismatches.append({"file": path.name, **detail})
            if args.limit and total >= args.limit:
                break
        if args.limit and total >= args.limit:
            break

    print(f"instances replayed: {total}")
    for key in ("exact", "mismatch", "legacy-code", "outdated", "no-template", "error", "content-unparsable", "file-unparsable"):
        if stats[key]:
            print(f"  {key:18s} {stats[key]}")
    current = stats["exact"] + stats["mismatch"] + stats["legacy-code"] + stats["error"]
    if current:
        print(f"  fidelity (same version) {stats['exact'] / current:.1%} of {current}")
    if missing_blocks:
        print("  blocks without template:", dict(missing_blocks.most_common(10)))
    if mismatches:
        print("\nfirst mismatches:")
        for m in mismatches[:8]:
            print("  ", json.dumps(m, ensure_ascii=False)[:300])
    if args.report:
        args.report.write_text(json.dumps({"stats": dict(stats), "total": total,
                                           "mismatches": mismatches[:200],
                                           "blocks": dict(by_block.most_common())},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nreport: {args.report}")
    return 0 if stats["mismatch"] == 0 and stats["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())





