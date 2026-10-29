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

from sherpa_xml.console import utf8_stdio


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


def _read_7bit(data: bytes, offset: int) -> tuple[int, int]:
    """Прочитать 7-bit length (LEB128) из data с offset."""
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("7-bit длина обрывается")
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, offset
        shift += 7


def decode_binary_container(b64_value: str, hex_limit: int) -> None:
    """Пытаемся декодировать Base64 и показать побайтовую структуру контейнера.

    Формат из main.md §11.1: PREFIX + encode_7bit(len(UTF8(ps_literal))) + UTF8 + 0x0B.
    Длину ищем с конца: перебираем k = длине литерала и позицию 7-bit префикса
    (1–5 байт; старый вариант поддерживал только k < 128).
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
    for k in range(len(body), 0, -1):
        lit = body[-k:]
        try:
            decoded = lit.decode("utf-8")
        except UnicodeDecodeError:
            continue
        end = len(body) - k
        # 7-bit длина может занимать до 5 байт; проверяем все варианты начала.
        for start in range(max(0, end - 5), end):
            try:
                length, after = _read_7bit(body, start)
            except ValueError:
                continue
            if length == k and after == end:
                prefix = body[:start]
                print(f"    7-bit длина: {length} ({end - start} байт) — подтверждена")
                print(f"    PS-литерал (UTF-8, {k} байт): {decoded!r}")
                print(f"    PREFIX ({len(prefix)} байт): {prefix.hex(' ')}")
                return
    print("    [!] Литерал с конца не вычленяется — разбор вручную по дампу выше.")


def decode_collection(container: str, vtype: str) -> None:
    """Разобрать List/Hashtable/DataTable через сериализаторы скилла."""
    try:
        from sherpa_xml.types import collections, data_table
    except Exception as exc:  # noqa: BLE001
        print(f"    [!] нет доступа к sherpa_xml.types: {exc}")
        return
    try:
        if vtype == "List":
            print(f"    List: {collections.decode_list(container)!r}")
        elif vtype == "Hashtable":
            print(f"    Hashtable: {collections.decode_hashtable(container)!r}")
        elif vtype == "DataTable":
            schema, diffgram = data_table.decode(container)
            print(f"    DataTable XmlSchema ({len(schema)} байт): {schema[:300]!r}")
            print(f"    DataTable XmlDiffGram ({len(diffgram)} байт): {diffgram[:300]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"    [!] контейнер {vtype} не разобран: {exc}")


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
        # Внутренние теги шаблона несут XAML-namespace {clr-namespace:...};
        # ищем по локальному имени тега.
        def local(tag: str) -> str:
            return tag.split("}")[-1]
        filled = [
            (e.get("Name"), e.get("Value"), e.get("Type"), e.get("ValueType"))
            for e in inner.iter()
            if local(e.tag) == "Property"
            and e.get("Value")
            and e.get("Name") not in ("ErrorLevel", "ErrorDirection", "ReportLevel", "ErrorMessage")
        ]
        if deep:
            print(
                f"  {iname:28} v{ver:<5} {item.findtext('Width','?')}x{item.findtext('Height','?')} "
                f"@({left},{top}) ID={did[:8]}… DisplayName={inner.get('DisplayName','')!r}"
            )
            for name, value, ptype, vtype in filled:
                print(f"      {name}: {str(value)[:70]!r} (Type={ptype}, VT={vtype})")
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
        if deep and vtype == "Text" and dv:
            print(f"    DefaultValue (первые 80): {dv[:80]!r}")
            decode_binary_container(dv, hex_limit)
        if deep and vtype in ("List", "Hashtable", "DataTable") and dv:
            print(f"    DefaultValue (первые 80): {dv[:80]!r}")
            decode_collection(dv, vtype)


def main() -> None:
    utf8_stdio()
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
