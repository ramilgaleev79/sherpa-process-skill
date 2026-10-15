"""Хелпер чтения golden-файлов для тестов (pure stdlib)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

GOLDEN_REAL = Path(__file__).resolve().parents[1] / "golden" / "real"


def diagram_properties(filename: str) -> list[ET.Element]:
    root = ET.parse(GOLDEN_REAL / filename).getroot()
    return root.findall(".//DiagramProperty")


def text_variables(filename: str = "b05-text-variants.process") -> list[tuple[str, str]]:
    """[(name, DefaultValue-Base64)] всех Text-переменных golden-файла."""
    return [
        (p.get("Name", ""), p.get("DefaultValue", ""))
        for p in diagram_properties(filename)
        if p.get("Type") == "Text"
    ]
