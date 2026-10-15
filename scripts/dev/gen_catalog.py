#!/usr/bin/env python3
"""Генератор каталогов block-catalog.yaml и type-catalog.yaml из golden-файлов.

Источник истины (D5 PLAN.md) — только эталонные .process в golden/real/.
Каталоги генерируются, не редактируются руками; повторная генерация должна
быть байт-стабильной (детерминизм: сортировка, фиксированный порядок ключей).

Запуск (из корня репо, venv с pyyaml):
    .venv/Scripts/python.exe scripts/dev/gen_catalog.py
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden" / "real"
BLOCK_CATALOG = REPO / "references" / "block-catalog.yaml"
TYPE_CATALOG = REPO / "references" / "type-catalog.yaml"

COMMON_ERROR_PROPS = ("ErrorLevel", "ErrorDirection", "ReportLevel", "ErrorMessage")


def local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_block(di: ET.Element) -> dict | None:
    """Извлекает структуру блока из DesignerItem."""
    content = di.find("Content")
    if content is None or not (content.text or "").strip():
        return None
    try:
        inner = ET.fromstring(content.text)
    except ET.ParseError:
        return None

    ports = []
    for c in inner.iter():
        if local(c.tag) == "ConnectorSettings":
            ports.append(
                {
                    "name": c.get("Name", ""),
                    "orientation": c.get("Orientation", ""),
                    "relative_position": c.get("RelativePosition", ""),
                    "max_in": c.get("MaxInConnections", ""),
                    "max_out": c.get("MaxOutConnections", ""),
                    "caption": c.get("Caption", ""),
                }
            )

    props = {}
    for p in inner.iter():
        if local(p.tag) != "Property":
            continue
        name = p.get("Name", "")
        if name in COMMON_ERROR_PROPS:
            continue  # общие для всех блоков; фиксируются один раз в каталоге
        props[name] = {
            "type": p.get("Type", ""),
            "value_type": p.get("ValueType", ""),
            "default_value": p.get("DefaultValue", ""),
            "enum_variants": p.get("EnumVariants", "") or "",
            "caption": p.get("Caption", ""),
            "is_required": p.get("IsRequired", ""),
        }

    return {
        "internal_name": inner.get("InternalName", ""),
        "version": inner.get("Version", ""),
        "display_name": inner.get("DisplayName", ""),
        "linux_available": inner.get("LinuxAvailable", ""),
        "default_width": inner.get("Width", ""),
        "default_height": inner.get("Height", ""),
        "ports": ports,
        "properties": props,
    }


def collect() -> tuple[dict, dict]:
    """Собирает каталоги: блоки по (internal_name, version), типы переменных."""
    blocks: dict[tuple[str, str], dict] = {}
    files_by_block: dict[tuple[str, str], set[str]] = defaultdict(set)
    var_types: dict[str, dict] = {}

    for path in sorted(GOLDEN.glob("*.process")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for di in root.findall(".//DesignerItem"):
            info = parse_block(di)
            if not info:
                continue
            key = (info["internal_name"], info["version"])
            files_by_block[key].add(path.name)
            if key in blocks:
                continue  # первый встреченный образец — канонический шаблон
            blocks[key] = info
        for p in root.findall(".//DiagramProperty"):
            t = p.get("Type", "")
            entry = var_types.setdefault(
                t,
                {
                    "observed_in": set(),
                    "is_global_observed": p.get("IsGlobal", ""),
                    "example_default_value": (p.get("DefaultValue", "") or "")[:64],
                },
            )
            entry["observed_in"].add(path.name)

    # ---- block-catalog.yaml ----
    catalog_blocks = {}
    incompat: list[str] = []
    by_name: dict[str, list[str]] = defaultdict(list)
    for (iname, ver) in sorted(blocks):
        by_name[iname].append(ver)
    for iname in sorted(by_name):
        versions = sorted(by_name[iname], key=int)
        entries = {v: blocks[(iname, v)] for v in versions}
        # эволюция портов/свойств между версиями — предупреждение в шапке отчёта
        refs = [
            (v, frozenset(p["name"] for p in e["ports"]), frozenset(e["properties"]))
            for v, e in entries.items()
        ]
        if len({(ports, props) for _, ports, props in refs}) > 1:
            incompat.append(iname)
        body = {
            "internal_name": iname,
            "display_name": entries[versions[0]]["display_name"],
            "linux_available": entries[versions[0]]["linux_available"],
            "default_size": {
                "width": entries[versions[0]]["default_width"],
                "height": entries[versions[0]]["default_height"],
            },
            "confirmed_versions": versions,
            "ports": entries[versions[0]]["ports"],
            "properties": entries[versions[0]]["properties"],
            "version_differences": {
                v: {
                    "ports": entries[v]["ports"],
                    "properties": entries[v]["properties"],
                }
                for v in versions[1:]
                if (
                    entries[v]["ports"] != entries[versions[0]]["ports"]
                    or entries[v]["properties"] != entries[versions[0]]["properties"]
                )
            },
            "golden_files": sorted(files_by_block[(iname, versions[0])]),
        }
        catalog_blocks[iname] = body

    catalog = {
        "meta": {
            "designer_version": "1.4.4",
            "generated_from": "golden/real/*.process",
            "generator": "scripts/dev/gen_catalog.py",
            "common_properties_all_blocks": {
                "ErrorLevel": {"type": "Enum", "enum_variants": "Default;Ignore;Handle"},
                "ErrorDirection": {"type": "Enum", "enum_variants": "Default;Right;Bottom"},
                "ReportLevel": {"type": "Enum", "enum_variants": "Default;Release;Debug;Detailed;Output"},
                "ErrorMessage": {"type": "Variable", "default_value": "$ErrorMessage"},
            },
        },
        "blocks": catalog_blocks,
    }

    # ---- type-catalog.yaml ----
    runtime_types = {
        "Number": "System.Double/Int (плоская строка в декларации)",
        "Logical": "System.Boolean (True/False; $True/$False в выражениях)",
        "Text": "бинарный контейнер Base64 (см. references/text-serialization.md)",
        "SecureString": "System.Security.SecureString (пустая строка в декларации)",
        "Hashtable": "System.Collections.Hashtable (BinaryFormatter-контейнер)",
        "Dictionary": "System.Collections.Generic.Dictionary`2[[System.Object],[System.Object]]",
        "List": "System.Collections.Generic.List`1[[System.String]]",
        "DataTable": "System.Data.DataTable (xs:schema + DiffGram, references/datatable-serialization.md)",
    }
    types_catalog = {
        "meta": {"designer_version": "1.4.4", "generated_from": "golden/real/*.process"},
        "variable_types": {
            t: {
                "runtime": runtime_types.get(t, "не зафиксировано"),
                "observed_in": sorted(v["observed_in"]),
            }
            for t, v in sorted(var_types.items())
        },
    }
    return catalog, types_catalog, incompat


def main() -> None:
    catalog, types_catalog, incompat = collect()
    BLOCK_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    BLOCK_CATALOG.write_text(
        yaml.dump(catalog, allow_unicode=True, sort_keys=True, default_flow_style=False, width=100),
        encoding="utf-8",
    )
    TYPE_CATALOG.write_text(
        yaml.dump(types_catalog, allow_unicode=True, sort_keys=True, default_flow_style=False, width=100),
        encoding="utf-8",
    )
    n = len(catalog["blocks"])
    versions_total = sum(len(b["confirmed_versions"]) for b in catalog["blocks"].values())
    print(f"block-catalog.yaml: {n} блоков, {versions_total} подтверждённых версий")
    print(f"type-catalog.yaml: {len(types_catalog['variable_types'])} типов переменных")
    if incompat:
        print(f"! Различия портов/свойств между версиями (см. version_differences): {', '.join(incompat)}")


if __name__ == "__main__":
    main()
