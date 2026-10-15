#!/usr/bin/env python3
"""Конвейер добавления нового блока из .process (PLAN.md, фаза 2.5 — Ingest).

Шаги:
    1. Копирует файл в golden/real/<имя>.process.
    2. Перегенерирует каталоги (gen_catalog.py).
    3. Сравнивает каталог с ir.NODE_TYPES и отчётит:
       - новые блоки (в каталоге, нет в NODE_TYPES) + готовая строка для вставки;
       - новые версии известных блоков (попадают в confirmed_versions автоматически);
       - конфликт-предупреждения (разные порты/свойства версий — version_differences).

Логическое имя IR выбирает разработчик (design-решение); тест
test_catalog_sync не даст забыть. Без golden-файла блок в генератор не попадает.

Запуск: .venv/Scripts/python.exe scripts/dev/ingest_block.py FILE.process [golden-имя]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from sherpa_xml import ir  # noqa: E402


def local(tag: str) -> str:
    return tag.split("}")[-1]


def blocks_in_file(path: Path) -> list[tuple[str, str]]:
    root = ET.parse(path).getroot()
    out = []
    for di in root.findall(".//DesignerItem"):
        content = di.find("Content")
        if content is None or not (content.text or "").strip():
            continue
        try:
            inner = ET.fromstring(content.text)
        except ET.ParseError:
            continue
        out.append((inner.get("InternalName", "?"), inner.get("Version", "?")))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    golden_name = sys.argv[2] if len(sys.argv) > 2 else src.stem.lower().replace(" ", "-")
    if not golden_name.endswith(".process"):
        golden_name += ".process"
    dst = REPO / "golden" / "real" / golden_name
    if dst.exists():
        print(f"!! {dst.name} уже существует — выберите другое имя")
        return 1
    shutil.copy(src, dst)
    print(f"1/3 Скопировано: {dst}")

    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "gen_catalog.py")],
        capture_output=True, text=True,
    )
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr)
        return 1

    import yaml  # dev-venv

    catalog = yaml.safe_load((REPO / "references" / "block-catalog.yaml").read_text(encoding="utf-8"))["blocks"]
    known_physical = set(ir.NODE_TYPES.values())
    file_blocks = blocks_in_file(dst)

    new_blocks = sorted({b for b, _ in file_blocks if b not in catalog or True} - {b for b in known_physical})
    # блоки, которые в файле есть, в каталоге появились, но IR-маппинга нет:
    unmapped = sorted({(b, v) for b, v in file_blocks if b not in known_physical})
    known_here = sorted({(b, v) for b, v in file_blocks if b in known_physical})

    print(f"\n3/3 Сверка с ir.NODE_TYPES:")
    if known_here:
        print("  Уже известны (проверьте версии):")
        for b, v in known_here:
            vers = catalog.get(b, {}).get("confirmed_versions", [])
            marker = "новая версия!" if v not in vers else "ok"
            print(f"    {b:24} v{v} — каталог: v{vers} [{marker}]")
    if unmapped:
        print("  НОВЫЕ блоки — добавьте логические имена в ir.NODE_TYPES:")
        for b, v in unmapped:
            suggestion = b  # логическое имя по умолчанию = физическое
            print(f'    "{suggestion}": "{b}",  # v{v}; порты: ' +
                  ", ".join(p["name"] for p in catalog[b]["ports"]))
        print("  После правки ir.py запустите: pytest tests/unit/test_catalog_sync.py -q")
    if not unmapped and not known_here:
        print("  (в файле нет ToolboxItemSettings-блоков)")

    versions_diff = [b for b, meta in catalog.items() if meta.get("version_differences")]
    if versions_diff:
        print(f"\n! Внимание — версии с различающимися портами/свойствами: {', '.join(versions_diff)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
