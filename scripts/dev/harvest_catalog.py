r"""Harvest the Sherpa RPA block catalog from the installed Designer (dev tool, stdlib only).

Sources (on the machine where Sherpa RPA is installed):
  <APP>\Designer\ExternalToolboxes\MainToolbox.xaml     — 752 blocks: Code, Version, properties, ports
  <APP>\Designer\ExternalToolboxes\Toolboxes-<lang>.json — 746 palette entries: localized captions/tooltips

Outputs (into the skill):
  assets/block_templates.json — canonical block templates for sherpa_xml.compiler
                                  {version, content, property_defaults, default_size, source}
  assets/block_index.json     — lightweight index: name -> {version, display, palette, group}
  references/block-catalog.yaml — generated human/agent-readable catalog

Design notes (see references/catalog-harvest.md):
  * `Content` of a real .process uses <Property Name="..."> for properties (24 194 / 0 named
    in the 211-.process corpus) — that is the canonical form emitted here.
  * Connector elements are normalized to <ConnectorSettings Name="..."> because
    sherpa_xml.compiler._parse_ports and every existing template use that form.
  * The 50 pre-existing "golden" templates are preserved verbatim (merge, not replace):
    they are the byte-proven baseline; harvesting only adds the remaining 702.
  * Determinism: keys sorted, no timestamps, source = file sha256 prefix.

Usage:
  python scripts/dev/harvest_catalog.py                 # harvest + merge + write
  python scripts/dev/harvest_catalog.py --check         # compare against existing templates
  python scripts/dev/harvest_catalog.py --no-embed-localization
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP = Path.home() / "AppData" / "Roaming" / "Sherpa RPA"

ATTR_ORDER = [
    "DisplayName", "InternalName", "FriendlyName", "Width", "Height", "PathToImage",
    "PathToToolboxImage", "ImageType", "NoDelete", "Invisible", "Proportional", "Version",
    "LinuxAvailable", "Code", "ProtoName", "TextColor", "Language", "UrlVideo", "DerivedFrom",
    "NoResize", "Container", "MouseOverColor", "MouseOverFill", "IsExpressionOnPause",
    "ExpressionOnPause", "IsCollapsedBlock", "Breakpoint", "DirivedFrom",
]
CONST_ATTRS = {
    "NoDelete": "False", "Invisible": "False", "Proportional": "True", "ProtoName": "",
    "TextColor": "#FF000000", "Language": "", "UrlVideo": "{x:Null}", "DerivedFrom": "",
    "NoResize": "False", "Container": "False", "MouseOverColor": "Gray",
    "MouseOverFill": "Transparent", "IsExpressionOnPause": "False", "ExpressionOnPause": "",
    "IsCollapsedBlock": "False", "Breakpoint": "False", "DirivedFrom": "",
    "PathToToolboxImage": "{x:Null}",
}
PROP_ATTRS = ["Name", "Tooltip", "CatalogName", "Type", "Value", "Visible", "DefaultValue",
              "ValueType", "EnumVariants", "Caption", "ProtoName", "ReadOnly", "IsRequired",
              "IsPSVerified"]
PROP_INSTANCE_DEFAULTS = {"Visible": "True", "DefaultValue": "", "ProtoName": "",
                          "ReadOnly": "False", "IsRequired": "False", "IsPSVerified": "True"}
CONN_POS = {"Top": "0.5,0", "Bottom": "0.5,1", "Left": "0,0.5", "Right": "1,0.5"}
XMLNS = (' xmlns="clr-namespace:ToolboxDesigner.Core;assembly=DiagramDesigner"'
         ' xmlns:av="http://schemas.microsoft.com/winfx/2006/xaml/presentation"'
         ' xmlns:s="clr-namespace:System;assembly=mscorlib"'
         ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def esc(value: str) -> str:
    """Attribute-value escaping used inside Content (matches Designer: &#xA; for newlines)."""
    if value is None:
        return ""
    out = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return out.replace("\r", "&#xD;").replace("\n", "&#xA;")


def sha256_prefix(path: Path, n: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


# ---------------------------------------------------------------- sources

def parse_xaml(xaml_path: Path) -> dict[str, dict]:
    tree = ET.parse(xaml_path)
    blocks: dict[str, dict] = {}
    for node in tree.getroot().iter():
        if "InternalName" not in node.attrib:
            continue
        code = ""
        props: list[dict] = []
        conns: list[dict] = []
        for child in node:
            ln = local(child.tag)
            if ln.endswith("Code"):
                code = child.text or ""
            elif ln.endswith("Properties"):
                for coll in child:
                    for p in coll:
                        if local(p.tag) != "Property":
                            continue
                        props.append({k: (p.get(k) or "") for k in PROP_ATTRS})
            elif ln.endswith("ConnectorsSettings"):
                for coll in child:
                    for c in coll:
                        conns.append({
                            "name": c.get("Name") or "",
                            "orientation": c.get("Orientation") or "Top",
                            "max_in": c.get("MaxInConnections") or "0",
                            "max_out": c.get("MaxOutConnections") or "0",
                            "caption": c.get("Caption") or "",
                        })
        if not code and not props and not conns:
            continue  # toolbox group node
        blocks[node.get("InternalName", "")] = {
            "attrs": dict(node.attrib),
            "code": code,
            "props": props,
            "conns": conns,
        }
    return blocks


def load_palette(path: Path) -> dict[str, dict]:
    """InternalName -> {display, group, tooltips: {prop: tooltip}} (localized)."""
    palette: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    for group in data:
        group_name = group.get("Name") or ""
        for item in group.get("ItemsSettings") or []:
            name = item.get("InternalName")
            if not name:
                continue
            tooltips = {}
            for p in item.get("Properties") or []:
                if p.get("Name"):
                    tooltips[p["Name"]] = p.get("Tooltip") or ""
            palette.setdefault(name, {
                "display": item.get("DisplayName") or "",
                "friendly": item.get("FriendlyName") or "",
                "group": group_name,
                "tooltips": tooltips,
                "captions": {p["Name"]: (p.get("Caption") or "") for p in (item.get("Properties") or []) if p.get("Name")},
            })
    return palette


# ---------------------------------------------------------------- build

def build_content(block: dict, display: str, friendly: str, tooltips: dict) -> str:
    attrs = {
        "DisplayName": display,
        "InternalName": block["attrs"].get("InternalName", ""),
        "FriendlyName": friendly,
        "Width": block["attrs"].get("Width") or "200",
        "Height": block["attrs"].get("Height") or "75",
        "PathToImage": block["attrs"].get("PathToImage") or "",
        "ImageType": block["attrs"].get("ImageType") or "LeftImage",
        "Version": block["attrs"].get("Version") or "",
        "LinuxAvailable": block["attrs"].get("LinuxAvailable") or "True",
        "Code": block["code"],
    }
    attrs.update(CONST_ATTRS)
    parts = ["<ToolboxItemSettings"]
    for name in ATTR_ORDER:
        parts.append(f' {name}="{esc(attrs.get(name, ""))}"')
    parts.append(XMLNS)
    parts.append(">")

    if block["props"]:
        parts.append(f'<ToolboxItemSettings.Properties><PropertiesCollection Capacity="{len(block["props"])}">')
        for p in block["props"]:
            row = dict(p)
            row["Tooltip"] = row.get("Tooltip") or tooltips.get(row["Name"], "")
            # EnumVariants: "" and "{x:Null}" are the same "no variants" sentinel — Designer
            # writes both (167 vs 142 occurrences across the 50 golden templates), so emit "".
            # XAML has no IsRequired; the catalog convention is CatalogName="Required" (verified 50/50)
            if row.get("IsRequired", "") == "":
                row["IsRequired"] = "True" if row.get("CatalogName") == "Required" else "False"
            for key, default in PROP_INSTANCE_DEFAULTS.items():
                if row.get(key, "") == "":
                    row[key] = default
            rendered = " ".join(f'{k}="{esc(row.get(k, ""))}"' for k in PROP_ATTRS)
            parts.append(f"<Property {rendered} />")
        parts.append("</PropertiesCollection></ToolboxItemSettings.Properties>")

    if block["conns"]:
        parts.append(f'<ToolboxItemSettings.ConnectorsSettings><ConnectorsSettingsCollection Capacity="{len(block["conns"])}">')
        for c in block["conns"]:
            pos = CONN_POS.get(c["orientation"], "0.5,0")
            parts.append(
                f'<ConnectorSettings Name="{esc(c["name"])}" Orientation="{esc(c["orientation"])}"'
                f' RelativePosition="{pos}" MaxInConnections="{c["max_in"]}"'
                f' MaxOutConnections="{c["max_out"]}" Color="#FF000000" Caption="{esc(c["caption"])}" />'
            )
        parts.append("</ConnectorsSettingsCollection></ToolboxItemSettings.ConnectorsSettings>")

    parts.append("</ToolboxItemSettings>")
    return "".join(parts)


def build_entry(name: str, block: dict, palette: dict[str, dict], source: str) -> dict:
    meta = palette.get(name, {})
    display = meta.get("display") or block["attrs"].get("DisplayName", "")
    friendly = meta.get("friendly") or block["attrs"].get("FriendlyName", "")
    content = build_content(block, display, friendly, meta.get("tooltips") or {})
    ET.fromstring(content)  # must stay well-formed
    defaults = {p["Name"]: p.get("Value", "") for p in block["props"] if p.get("Name")}
    return {
        "version": block["attrs"].get("Version", ""),
        "content": content,
        "property_defaults": defaults,
        "default_size": {"width": block["attrs"].get("Width") or "200",
                         "height": block["attrs"].get("Height") or "75"},
        "source": source,
    }


def build_index_entry(name: str, block: dict, palette_entry: dict | None) -> dict:
    props = {
        p["Name"]: {
            "type": p.get("Type", ""),
            "value_type": "" if p.get("ValueType", "") in ("", "{x:Null}") else p.get("ValueType", ""),
            "enum": "" if p.get("EnumVariants", "") in ("", "{x:Null}") else p.get("EnumVariants", ""),
            "catalog": p.get("CatalogName", ""),
            "caption": p.get("Caption", ""),
            "required": p.get("IsRequired", ""),
        }
        for p in block["props"] if p.get("Name")
    }
    return {
        "version": block["attrs"].get("Version", ""),
        "display_name": (palette_entry or {}).get("display") or block["attrs"].get("DisplayName", ""),
        "display_name_en": block["attrs"].get("DisplayName", ""),
        "friendly_name": (palette_entry or {}).get("friendly") or block["attrs"].get("FriendlyName", ""),
        "palette": bool(palette_entry),
        "group": (palette_entry or {}).get("group", ""),
        "linux": block["attrs"].get("LinuxAvailable", ""),
        # full property/port metadata lives in block_templates.json content and block-catalog.yaml;
        # the index stays small because sherpa_xml.ir loads it on every CLI invocation.
        "properties": len(props),
        "ports": [c["name"] for c in block["conns"]],
    }


# ---------------------------------------------------------------- check

def canonical(content: str) -> dict:
    root = ET.fromstring(content)
    props = []
    conns = []
    for child in root:
        ln = local(child.tag)
        if ln.endswith("Properties"):
            for coll in child:
                for p in coll:
                    if local(p.tag) != "Property":
                        continue
                    props.append((p.get("Name", ""), p.get("Type", ""),
                                  "" if p.get("EnumVariants", "") in ("", "{x:Null}") else p.get("EnumVariants", ""), p.get("CatalogName", ""),
                                  p.get("IsRequired", "")))
        elif ln.endswith("ConnectorsSettings"):
            for coll in child:
                for c in coll:
                    conns.append((c.get("Name", ""), c.get("MaxInConnections", ""),
                                  c.get("MaxOutConnections", ""), c.get("Orientation", "")))
    return {
        "version": root.get("Version", ""),
        "display": root.get("DisplayName", ""),
        "size": (root.get("Width", ""), root.get("Height", "")),
        "code": re.sub(r"\s+", " ", root.get("Code", "")).strip(),
        "props": sorted(props),
        "conns": sorted(conns),
    }


def run_check(harvested: dict[str, dict], existing: dict[str, dict]) -> int:
    matched, diffs = [], []
    golden = {k for k, v in existing.items() if not str(v.get("source", "")).startswith("harvest:")}
    for name, entry in existing.items():
        if name not in harvested:
            diffs.append({"block": name, "reason": "not in toolbox"})
            continue
        a = canonical(harvested[name]["content"])
        b = canonical(entry["content"])
        delta = {k: (a[k], b[k]) for k in ("version", "display", "size") if a[k] != b[k]}
        if a["code"] != b["code"]:
            delta["code"] = "differs"
        if a["props"] != b["props"]:
            only_a = [p for p in a["props"] if p not in b["props"]][:3]
            only_b = [p for p in b["props"] if p not in a["props"]][:3]
            delta["props"] = {"harvest": only_a, "golden": only_b}
        if a["conns"] != b["conns"]:
            delta["conns"] = {"harvest": a["conns"], "golden": b["conns"]}
        if delta:
            diffs.append({"block": name, "reason": delta})
        else:
            matched.append(name)
    print(f"check: existing={len(existing)} matched={len(matched)} diff={len(diffs)} "
          f"(golden-sourced: {len(golden)})")
    for d in diffs[:10]:
        print("  DIFF", json.dumps(d, ensure_ascii=False)[:400])
    golden_diffs = [d for d in diffs if d["block"] in golden]
    if golden_diffs:
        print(f"  golden regressions: {len(golden_diffs)}")
        return 1
    return 0 if not diffs else 1


# ---------------------------------------------------------------- emit

def emit_yaml(catalog: dict[str, dict], index: dict[str, dict], meta: dict) -> str:
    lines = [
        "# GENERATED FILE — do not edit by hand.",
        "# Source: Designer/ExternalToolboxes/MainToolbox.xaml + Toolboxes-<lang>.json",
        f"# designer_toolbox_sha256: {meta['designer_toolbox_sha256']}",
        f"# blocks: {meta['count']}  palette: {meta['palette_count']}  generated_by: scripts/dev/harvest_catalog.py",
        "blocks:",
    ]
    for name in sorted(catalog):
        e = catalog[name]
        i = index[name]
        lines.append(f"  {name}:")
        lines.append(f"    internal_name: {name}")
        lines.append(f"    display_name: {json.dumps(i['display_name'], ensure_ascii=False)}")
        lines.append(f"    display_name_en: {json.dumps(i['display_name_en'], ensure_ascii=False)}")
        lines.append(f"    confirmed_versions: ['{i['version']}']")
        lines.append(f"    palette: {'true' if i['palette'] else 'false'}")
        if i["group"]:
            lines.append(f"    group: {json.dumps(i['group'], ensure_ascii=False)}")
        lines.append(f"    default_size: {{height: '{e['default_size']['height']}', width: '{e['default_size']['width']}'}}")
        lines.append(f"    linux_available: '{i['linux']}'")
        root = ET.fromstring(e["content"])
        ports, props = [], []
        for child in root:
            ln = local(child.tag)
            if ln.endswith("ConnectorsSettings"):
                for coll in child:
                    for c in coll:
                        ports.append(c)
            elif ln.endswith("Properties"):
                for coll in child:
                    for p in coll:
                        props.append(p)
        if ports:
            lines.append("    ports:")
            for c in ports:
                lines.append(
                    f"    - {{max_in: '{c.get('MaxInConnections', '0')}', max_out: '{c.get('MaxOutConnections', '0')}', "
                    f"name: {c.get('Name', '')}, orientation: {c.get('Orientation', '')}}}"
                )
        if props:
            lines.append("    properties:")
            for p in sorted(props, key=lambda x: x.get("Name", "")):
                fields = [f"type: {p.get('Type', '') or 'Expression'}"]
                vt = p.get("ValueType", "")
                if vt and vt != "{x:Null}":
                    fields.append(f"value_type: {vt}")
                ev = p.get("EnumVariants", "")
                if ev and ev != "{x:Null}":
                    fields.append(f"enum: {json.dumps(ev, ensure_ascii=False)}")
                cat = p.get("CatalogName", "")
                if cat:
                    fields.append(f"catalog: {json.dumps(cat, ensure_ascii=False)}")
                lines.append(f"      {p.get('Name', '')}: {{{', '.join(fields)}}}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest Sherpa RPA block catalog from an installed Designer")
    ap.add_argument("--app-root", type=Path, default=DEFAULT_APP, help="…/AppData/Roaming/Sherpa RPA")
    ap.add_argument("--lang", default="ru", help="palette localization (ru, pt, en → Toolboxes-<lang>.json)")
    ap.add_argument("--skill-root", type=Path, default=SKILL_ROOT)
    ap.add_argument("--check", action="store_true", help="compare harvested templates with existing ones")
    ap.add_argument("--no-preserve", action="store_true", help="do not keep pre-existing golden templates")
    args = ap.parse_args(argv)

    toolbox_dir = args.app_root / "Designer" / "ExternalToolboxes"
    xaml_path = toolbox_dir / "MainToolbox.xaml"
    palette_path = toolbox_dir / f"Toolboxes-{args.lang}.json"
    if not xaml_path.exists():
        print(f"error: {xaml_path} not found — pass --app-root", file=sys.stderr)
        return 2
    palette = load_palette(palette_path) if palette_path.exists() else {}
    blocks = parse_xaml(xaml_path)
    source = f"harvest:MainToolbox.xaml@{sha256_prefix(xaml_path)}"

    harvested = {name: build_entry(name, block, palette, source) for name, block in blocks.items()}
    index = {name: build_index_entry(name, block, palette.get(name)) for name, block in blocks.items()}

    templates_path = args.skill_root / "assets" / "block_templates.json"
    existing = json.loads(templates_path.read_text(encoding="utf-8")) if templates_path.exists() else {}

    if args.check:
        index_path = args.skill_root / "assets" / "block_index.json"
        try:
            meta = json.loads(index_path.read_text(encoding="utf-8"))["meta"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"check: нет валидного block_index.json: {exc}")
            return 1
        current_hash = sha256_prefix(xaml_path)
        if meta.get("designer_toolbox_sha256") != current_hash:
            print(f"check: STALE index: meta={meta.get('designer_toolbox_sha256')!r} "
                  f"current={current_hash!r} — перегенерируйте каталог")
            return 1
        return run_check(harvested, existing)

    merged = dict(harvested)
    preserved = 0
    if not args.no_preserve:
        for name, entry in existing.items():
            if name not in merged:
                continue
            # Preserve only byte-proven golden entries. Harvested entries must be
            # refreshed from the current toolbox; otherwise a stale catalog can
            # never be repaired by running the harvester again.
            if str(entry.get("source", "")).startswith("harvest:"):
                continue
            merged[name] = entry
            merged[name].setdefault("source", "golden")
            preserved += 1

    templates_path.parent.mkdir(parents=True, exist_ok=True)
    templates_path.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")

    meta = {
        "designer_toolbox_sha256": sha256_prefix(xaml_path),
        "palette": args.lang,
        "count": len(merged),
        "palette_count": sum(1 for i in index.values() if i["palette"]),
        "preserved_golden": preserved,
        "source": source,
    }
    index_path = args.skill_root / "assets" / "block_index.json"
    index_path.write_text(json.dumps({"meta": meta, "blocks": dict(sorted(index.items()))},
                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    yaml_path = args.skill_root / "references" / "block-catalog.yaml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(emit_yaml(merged, index, meta), encoding="utf-8")

    print(f"harvested={len(harvested)} preserved_golden={preserved} palette={meta['palette_count']}")
    print(f"wrote {templates_path} ({templates_path.stat().st_size} B)")
    print(f"wrote {index_path} ({index_path.stat().st_size} B)")
    print(f"wrote {yaml_path} ({yaml_path.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())








