#!/usr/bin/env python3
"""Инспектор golden-файлов .process для скила sherpa-process.

Разбирает реальный .process Sherpa RPA Designer и выводит структурную сводку:
секции корня, блоки (InternalName/Version), layout, связи графа,
переменные и побайтовый формат бинарных контейнеров начальных значений.

Pure stdlib (D1). Использование:
    python golden_inspector.py <file.process> [...] [--deep] [--hex LEN]
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def indent(depth: int) -> str:
    return "  " * depth


def parse_inner_content(content: str) -> ET.Element | None:
    """Content DesignerItem — экранированный XML ToolboxItemSettings; парсим его."""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def hexdump(data: bytes, limit: int) -> str:
    lines = []
    for i in range(0, min(len(data), limit), 16):
        chunk = data[i : i + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"    {i:04x}  {hexpart:<47}  {asciipart}")
    if len(data) > limit:
        lines.append(f"    ... ({len(data)} bytes total)")
    return "\n".join(lines)


def decode_binary_container(b64_value: str, hex_limit: int) -> None:
    """Пытаемся декодировать Base64 и показать побайтовую структуру контейнера.

    Формат из main.md §11.1: PREFIX + encode_7bit(len(UTF8(ps_literal))) + UTF8 + 0x0B.
    Длину ищем с конца: перебираем k = длине литерала, проверяем 7-bit длину перед ним.
    """
    try:
        raw = base64.b64decode(b64_value, validate=True)
    except Exception as exc:
        print(f"    [!] Base64 невалиден: {exc}")
        return
    print(f"    Base64 OK, {len(raw)} байт декодировано:")
    print(hexdump(raw, hex_limit))
    if not raw.endswith(b"\x0b"):
        print("    Терминатор 0x0B не найден — возможно, другой формат контейнера.")
        return
    body = raw[:-1]
    # перебор гипотез: литерал = последние k байт body; перед ним — 7-bit длина
    for k in range(len(body), 0, -1):
        lit = body[-k:]
        try:
            text = lit.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # 7-bit длина перед литералом: 1 байт, если k < 0x80
        if k < 0x80 and len(body) >= k + 1 and body[-k - 1] == k:
            prefix = body[: len(body) - k - 1]
            print(f"    7-bit длина: {body[-k-1]:#04x} (= {k}) — подтверждена")
            print(f"    PS-литерал (UTF-8, {k} байт): {text!r}")
            print(f"    PREFIX ({len(prefix)} байт): {prefix.hex(' ')}")
            return
    print("    [!] Литерал с конца не вычленяется — разбор вручную по дампу выше.")


def inspect(path: Path, deep: bool, hex_limit: int) -> None:
    print(f"{'=' * 72}")
    print(f"ФАЙЛ: {path}")
    print(f"{'=' * 72}")

    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        print("BOM: да (utf-8-sig)")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"[!] XML не парсится: {exc}")
        return
    root = tree.getroot()

    # --- Секции корня ---
    print("\n[Секции корня]")
    for child in root:
        n = len(list(child))
        print(f"  {child.tag}: {n} детей" + (f"  (текст: {child.text!r:.60})" if n == 0 else ""))
    attrs = root.attrib
    if attrs:
        print(f"  Атрибуты корня: {attrs}")

    # --- DesignerItems (блоки + layout) ---
    blocks = root.findall(".//DesignerItem")
    print(f"\n[Блоки DesignerItem: {len(blocks)}]")
    seen_versions: dict[str, list[str]] = {}
    for item in blocks:
        content_el = item.find("Content")
        inner = parse_inner_content(content_el.text or "") if content_el is not None else None
        if inner is None:
            print("  [!] Content не парсится")
            continue
        iname = inner.get("InternalName", "?")
        ver = inner.get("Version", "?")
        seen_versions.setdefault(f"{iname}", []).append(ver)
        left = item.findtext("Left", "?")
        top = item.findtext("Top", "?")
        did = item.findtext("ID", "?")
        if deep:
            print(
                f"  {iname:28} v{ver:<5} {item.findtext('Width','?')}x{item.findtext('Height','?')} "
                f"@({left},{top}) ID={did[:8]}… DisplayName={inner.get('DisplayName','')!r}"
            )
    print("  Уникальные InternalName и их версии:")
    for iname in sorted(seen_versions):
        vers = sorted(set(seen_versions[iname]))
        print(f"    {iname}: v{'/'.join(vers)}")

    # --- Прочие дети DesignerItem (что кроме Content) ---
    if blocks:
        keys = [c.tag for c in blocks[0]]
        print(f"\n[Поля DesignerItem (порядок)]: {keys}")

    # --- Связи графа ---
    print("\n[Связи графа]")
    conns = root.findall("./Connections/Connection")
    print(f"  <Connections>: {len(conns)} связей; поля первой связи:")
    if conns:
        keys = [c.tag for c in conns[0]]
        print(f"    порядок полей: {keys}")
        for c in conns[:5]:
            parts = {c2.tag: (c2.text or "")[:12] for c2 in c}
            print(f"    {parts}")

    # --- Properties / DiagramProperty ---
    props = root.findall(".//DiagramProperty")
    print(f"\n[Переменные DiagramProperty: {len(props)}]")
    for p in props:
        name = p.get("Name", "?")
        vtype = p.get("Type", "?")
        dv = p.get("DefaultValue", "")
        val = p.get("Value", "")
        print(f"  {name!r:34} {vtype:14} IsGlobal={p.get('IsGlobal','?')}")
        if deep and vtype in ("Text", "SecureString"):
            if vtype == "Text" and dv:
                print(f"    DefaultValue (первые 80): {dv[:80]!r}")
                decode_binary_container(dv, hex_limit)
        if deep and vtype == "DataTable" and dv:
            print(f"    DefaultValue (первые 80): {dv[:80]!r}")
            decode_binary_container(dv, hex_limit)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--deep", action="store_true", help="подробно: все блоки, контейнеры переменных")
    ap.add_argument("--hex", type=int, default=64, help="сколько байт hex-дампа показывать")
    args = ap.parse_args()
    for f in args.files:
        inspect(f, args.deep, args.hex)
        print()


if __name__ == "__main__":
    sys.exit(main())
