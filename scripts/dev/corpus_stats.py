"""Статистика установленного корпуса .process (stdlib, dev/приёмка).

Порождает цифры, которые нельзя держать «на глаз» в SKILL.md: число связей
InputLeft, распределение портов/типов переменных, размер корпуса. Запуск:

    python scripts/dev/corpus_stats.py
    python scripts/dev/corpus_stats.py --json

По умолчанию сканирует `%APPDATA%\\Sherpa RPA` и `...\\Sherpa RPA Data`.
Результат зависит от установленного набора проектов — это не константа скилла.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_ROOTS = [
    Path.home() / "AppData" / "Roaming" / "Sherpa RPA",
    Path.home() / "AppData" / "Roaming" / "Sherpa RPA Data",
]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def collect(roots: list[Path]) -> dict:
    files = 0
    blocks = 0
    connections = 0
    sink_ports: collections.Counter = collections.Counter()
    source_ports: collections.Counter = collections.Counter()
    variable_types: collections.Counter = collections.Counter()
    text_values = 0
    text_nonempty = 0
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.process")):
            if path.name.startswith("~"):
                continue  # автосейв Designer — дубликат с устаревшим состоянием
            if path in seen:
                continue
            seen.add(path)
            try:
                tree = ET.parse(path)
            except ET.ParseError:
                continue
            files += 1
            doc = tree.getroot()
            for item in doc.iter():
                if _local(item.tag) != "DesignerItem":
                    continue
                content = item.findtext("Content") or ""
                try:
                    ET.fromstring(content)
                except ET.ParseError:
                    continue
                blocks += 1
            for conn in doc.iter():
                if _local(conn.tag) != "Connection":
                    continue
                connections += 1
                sink_ports[conn.findtext("SinkConnectorName") or ""] += 1
                source_ports[conn.findtext("SourceConnectorName") or ""] += 1
            for prop in doc.iter():
                if _local(prop.tag) != "DiagramProperty":
                    continue
                variable_types[prop.get("Type") or ""] += 1
                if prop.get("Type") == "Text":
                    text_values += 1
                    if prop.get("Value"):
                        text_nonempty += 1
    return {
        "roots": [str(p) for p in roots],
        "files": files,
        "blocks": blocks,
        "connections": connections,
        "input_left_links": sink_ports.get("InputLeft", 0),
        "sink_ports": dict(sink_ports.most_common()),
        "source_ports": dict(source_ports.most_common()),
        "variables": sum(variable_types.values()),
        "variable_types": dict(variable_types.most_common()),
        "text_values": text_values,
        "text_nonempty": text_nonempty,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, nargs="+", default=DEFAULT_ROOTS,
                    help="каталоги корпуса (по умолчанию AppData Sherpa RPA)")
    ap.add_argument("--json", action="store_true", help="машинный JSON")
    args = ap.parse_args(argv)
    stats = collect(args.root)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    else:
        print(f"файлов: {stats['files']}  блоков: {stats['blocks']}  связей: {stats['connections']}")
        print(f"InputLeft: {stats['input_left_links']}  Input: {stats['sink_ports'].get('Input', 0)}")
        print("sink ports:", ", ".join(f"{k}={v}" for k, v in stats["sink_ports"].items()))
        print("переменных:", stats["variables"], stats["variable_types"])
        print(f"Text непустых/всего: {stats['text_nonempty']}/{stats['text_values']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
