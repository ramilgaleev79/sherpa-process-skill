#!/usr/bin/env python3
"""Извлечение эталонного паттерна из golden .process (PLAN.md, фаза 2.5).

Выделяет подграф (выбранные блоки + связи между ними + все переменные) и пишет
ЧЕРНОВИК patterns/<name>/pattern.json с raw-данными: физические InternalName,
PS-значения свойств как есть. Черновик нормализуется в логический IR вручную
или агентом (выбор логических имён узлов, типизация выражений), затем
валидируется тестом и становится эталонным паттерном.

Запуск:
    .venv/Scripts/python.exe scripts/dev/extract_pattern.py FILE.process PATTERN_NAME [ID-префиксы через запятую]
    (без ID-префиксов — берутся все блоки, кроме Start/ReturnOrStop)
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_COMMON = {"ErrorLevel", "ErrorDirection", "ReportLevel", "ErrorMessage"}


def local(tag: str) -> str:
    return tag.split("}")[-1]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, name = Path(sys.argv[1]), sys.argv[2]
    id_prefixes = [p.strip().lower() for p in sys.argv[3].split(",")] if len(sys.argv) > 3 else []

    root = ET.parse(src).getroot()
    nodes, id_map = [], {}
    for di in root.findall(".//DesignerItem"):
        content = di.find("Content")
        if content is None or not (content.text or "").strip():
            continue
        inner = ET.fromstring(content.text)
        iname = inner.get("InternalName", "?")
        did = di.findtext("ID", "")
        if id_prefixes and not any(did.lower().startswith(p) for p in id_prefixes):
            continue
        if not id_prefixes and iname in ("Start", "ReturnOrStop"):
            continue
        props = {}
        for p in inner.iter():
            if local(p.tag) != "Property" or p.get("Name") in SKIP_COMMON:
                continue
            if p.get("Value"):
                props[p.get("Name")] = {"value": p.get("Value"), "type": p.get("Type"),
                                        "value_type": p.get("ValueType")}
        ports = {c.get("Name"): {"orientation": c.get("Orientation"),
                                  "max_out": c.get("MaxOutConnections")}
                 for c in inner.iter() if local(c.tag) == "ConnectorSettings"}
        key = f"{iname.lower()}_{did[:4]}"
        id_map[did] = key
        nodes.append({"key": key, "physical": iname, "version": inner.get("Version"),
                      "raw_properties": props, "ports": ports,
                      "layout": {"left": di.findtext("Left"), "top": di.findtext("Top"),
                                 "width": di.findtext("Width"), "height": di.findtext("Height")}})

    edges = []
    for c in root.findall("./Connections/Connection"):
        s, t = c.findtext("SourceID", ""), c.findtext("SinkID", "")
        if s in id_map and t in id_map:
            edges.append({"from": id_map[s], "from_port": c.findtext("SourceConnectorName", ""),
                          "to": id_map[t], "to_port": c.findtext("SinkConnectorName", "")})

    variables = []
    for p in root.findall(".//DiagramProperty"):
        variables.append({"name": p.get("Name"), "sherpa_type": p.get("Type"),
                          "is_global": p.get("IsGlobal")})

    draft = {
        "meta": {
            "name": name,
            "status": "DRAFT-extracted, нормализуйте в логический IR",
            "source_golden": src.name,
            "description": "ЗАПОЛНИТЕ: что делает паттерн и когда применять",
            "when_to_use": "ЗАПОЛНИТЕ",
            "entry": "ЗАПОЛНИТЕ: key входного узла (куда подключать предыдущий блок)",
            "exit": "ЗАПОЛНИТЕ: key:port выходного узла (к следующему)",
            "parameters": [],
        },
        "fragment": {"variables": variables, "nodes": nodes, "edges": edges},
    }
    out_dir = REPO / "patterns" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "pattern.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Черновик паттерна: {out}")
    print(f"  узлов: {len(nodes)}, связей: {len(edges)}, переменных: {len(variables)}")
    print("  Следующий шаг: нормализовать fragment в логический IR (см. PLAN.md 2.5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
