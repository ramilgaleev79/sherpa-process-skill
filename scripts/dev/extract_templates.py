#!/usr/bin/env python3
"""Извлечение канонических шаблонов блоков из golden → assets/block_templates.json.

Для каждого (InternalName, Version) берёт образец с МИНИМАЛЬНЫМ числом
заполненных Property/@Value (наиболее «чистый» шаблон Designer'а) и сохраняет:

    { internal_name: {
        "version": "…",
        "content": "<строка внутреннего XML ToolboxItemSettings как в golden>",
        "property_defaults": {имя свойства: DefaultValue},
        "default_size": {"width": …, "height": …},
        "source": "golden-файл образца"
    } }

Подстановка значений при генерации: Property/@Value := DefaultValue шаблона,
кроме свойств, задаваемых IR (compiler.py). Запуск из корня репо (venv):

    .venv/Scripts/python.exe scripts/dev/extract_templates.py
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden" / "real"
ASSETS = REPO / "assets"


def local(tag: str) -> str:
    return tag.split("}")[-1]


def main() -> int:
    # key=(iname, ver) → (score, content, defaults, size, source)
    best: dict[tuple[str, str], tuple] = {}
    for path in sorted(GOLDEN.glob("*.process")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for di in root.findall(".//DesignerItem"):
            content_el = di.find("Content")
            if content_el is None or not (content_el.text or "").strip():
                continue
            content = content_el.text  # уже разэкранированный внутренний XML
            try:
                inner = ET.fromstring(content)
            except ET.ParseError:
                continue
            iname = inner.get("InternalName", "")
            ver = inner.get("Version", "")
            defaults = {}
            filled = 0
            for p in inner.iter():
                if local(p.tag) != "Property":
                    continue
                defaults[p.get("Name", "")] = p.get("DefaultValue", "") or ""
                if p.get("Value"):
                    filled += 1
            score = (filled, path.name)
            key = (iname, ver)
            if key not in best or score < best[key][0]:
                best[key] = (
                    score,
                    content,
                    defaults,
                    {"width": inner.get("Width", ""), "height": inner.get("Height", "")},
                    path.name,
                )

    templates = {}
    for (iname, ver), (_score, content, defaults, size, source) in sorted(best.items()):
        if iname in templates:  # несколько версий: первую (по сортировке) считаем канонической
            continue
        templates[iname] = {
            "version": ver,
            "content": content,
            "property_defaults": defaults,
            "default_size": size,
            "source": source,
        }

    ASSETS.mkdir(exist_ok=True)
    out = ASSETS / "block_templates.json"
    out.write_text(json.dumps(templates, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(templates)} шаблонов → {out.relative_to(REPO)} "
          f"({out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
