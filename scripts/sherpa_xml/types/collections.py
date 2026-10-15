"""Контейнеры коллекций: List<String>, Hashtable (main.md §12–13, golden b07/b09).

Формат — BinaryFormatter-подобная сериализация Sherpa; формулы выведены
побайтово из golden и подтверждены round-trip (byte-equal).

Ограничения подтверждённости (single-sample риски, снятие — новыми golden):
- List: capacity = 4→8→16… (правило роста .NET) — подтверждено только n=5→8;
  константный блок member-enums — один образец.
- Hashtable: поля Version=5, HashSize=10, константный блок после LoadFactor —
  подтверждены только на n=4; порядок пар в golden — бакетный (не insertion):
  writer сохраняет переданный порядок как есть (см. §12.4 — runtime-порядок
  не гарантирован).
- Dictionary: encode/decode НЕ реализован (вложенные KeyValuePair`2-записи;
  нужен второй образец); пары читаются инспектором как текст.
"""

from __future__ import annotations

import base64
import struct

from .text import decode_7bit

HEADER = bytes.fromhex("0001000000ffffffff0100000000000000")

LIST_TYPE = (
    "System.Collections.Generic.List`1[[System.String, mscorlib, "
    "Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089]]"
)


def _bw7(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _bws(s: str) -> bytes:
    data = s.encode("utf-8")
    return _bw7(len(data)) + data


def _i32(n: int) -> bytes:
    return n.to_bytes(4, "little", signed=True)


def _read_bw_string(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = decode_7bit(data, offset)
    end = offset + length
    if end > len(data):
        raise ValueError("строка выходит за границы контейнера")
    return data[offset:end].decode("utf-8"), end


# --- List<String> ---


def encode_list(items: list[str]) -> str:
    """Список строк → Base64-контейнер (порядок элементов = порядок данных, §13.3)."""
    n = len(items)
    capacity = 0 if n == 0 else max(4, 1 << (n - 1).bit_length())
    out = bytearray(HEADER)
    out += b"\x04" + _i32(1) + _bws(LIST_TYPE)
    out += _i32(3) + _bws("_items") + _bws("_size") + _bws("_version")
    out += bytes.fromhex("060000080809")  # member enums (single-sample)
    out += _i32(2) + _i32(n) + _i32(n) + b"\x11" + _i32(2) + _i32(capacity)
    for i, item in enumerate(items):
        out += b"\x06" + _i32(3 + i) + _bws(item)
    out += b"\x0d\x03\x0b"
    return base64.b64encode(bytes(out)).decode("ascii")


def decode_list(container: str) -> list[str]:
    """Base64-контейнер → элементы (строгий парсер по формуле)."""
    raw = base64.b64decode(container, validate=True)
    pos = len(HEADER)
    if raw[pos : pos + 1] != b"\x04":
        raise ValueError("это не Class-контейнер коллекции")
    pos += 1
    _obj_id = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
    type_name, pos = _read_bw_string(raw, pos)
    if type_name != LIST_TYPE:
        raise ValueError(f"не List<String>: {type_name}")
    pos += 4  # memberCount=3
    for _ in range(3):
        _, pos = _read_bw_string(raw, pos)
    pos += 6  # member enums
    pos += 4  # items array id
    n = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
    pos += 4 + 1 + 4 + 4  # len, 0x11, ref, capacity
    items = []
    for _ in range(n):
        if raw[pos : pos + 1] != b"\x06":
            raise ValueError("ожидался ObjectString-элемент")
        pos += 1
        _item_id = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
        item, pos = _read_bw_string(raw, pos)
        items.append(item)
    if raw[pos:] != b"\x0d\x03\x0b":
        raise ValueError("битый хвост List-контейнера")
    return items


# --- Hashtable (Dictionary<Object,Object> — общий физический шаблон после доработки) ---


def encode_hashtable(pairs: list[tuple[str, str]]) -> str:
    """Пары (ключ, значение) в переданном порядке → Base64-контейнер.

    Порядок пар сохраняется как есть; runtime-порядок итерации не гарантируется
    (main.md §12.4). Поля Version=5/HashSize=10 — константы образца n=4.
    """
    n = len(pairs)
    out = bytearray(HEADER)
    out += b"\x04" + _i32(1) + _bws("System.Collections.Hashtable")
    out += _i32(7)
    for member in ("LoadFactor", "Version", "Comparer", "HashCodeProvider", "HashSize", "Keys", "Values"):
        out += _bws(member)
    out += bytes.fromhex("000003030005050b08")
    out += _bws("System.Collections.IComparer") + _bws("System.Collections.IHashCodeProvider")
    out += b"\x08" + struct.pack("<f", 0.72)  # LoadFactor (single!)
    out += _i32(5) + b"\x0a"  # Version, HashSize (single-sample константы)
    out += bytes.fromhex("0a0700000009") + _i32(2) + b"\x09" + _i32(3) + b"\x10" + _i32(2)
    out += _i32(n)
    for i, (key, _value) in enumerate(pairs):
        out += b"\x06" + _i32(4 + i) + _bws(key)
    out += b"\x10" + _i32(3)
    out += _i32(n)
    for i, (_key, value) in enumerate(pairs):
        out += b"\x06" + _i32(4 + n + i) + _bws(value)
    out += b"\x0b"
    return base64.b64encode(bytes(out)).decode("ascii")


def decode_hashtable(container: str) -> list[tuple[str, str]]:
    """Base64-контейнер → пары в байтовом порядке golden (ключи, затем значения)."""
    raw = base64.b64decode(container, validate=True)
    pos = len(HEADER) + 1 + 4
    type_name, pos = _read_bw_string(raw, pos)
    if type_name != "System.Collections.Hashtable":
        raise ValueError(f"не Hashtable: {type_name}")
    member_count = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
    members = []
    for _ in range(member_count):
        member, pos = _read_bw_string(raw, pos)
        members.append(member)
    if members != ["LoadFactor", "Version", "Comparer", "HashCodeProvider", "HashSize", "Keys", "Values"]:
        raise ValueError(f"неожидаемый набор членов Hashtable: {members}")
    keys: list[str] = []
    values: list[str] = []
    # до Keys-массива идут enums+comparer-строки+LoadFactor+константы;
    # надёжный якорь — маркер `10 02 00 00 00` перед количеством ключей
    anchor = b"\x10" + _i32(2)
    idx = raw.find(anchor, pos)
    if idx < 0:
        raise ValueError("не найден Keys-массив")
    pos = idx + len(anchor)
    n = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
    for _ in range(n):
        if raw[pos : pos + 1] != b"\x06":
            raise ValueError("ожидался ключ-строка")
        pos += 1
        pos += 4
        key, pos = _read_bw_string(raw, pos)
        keys.append(key)
    values_anchor = b"\x10" + _i32(3)
    idx = raw.find(values_anchor, pos)
    if idx < 0:
        raise ValueError("не найден Values-массив")
    pos = idx + len(values_anchor)
    n2 = int.from_bytes(raw[pos : pos + 4], "little"); pos += 4
    for _ in range(n2):
        if raw[pos : pos + 1] != b"\x06":
            raise ValueError("ожидалось значение-строка")
        pos += 1
        pos += 4
        value, pos = _read_bw_string(raw, pos)
        values.append(value)
    if n != n2:
        raise ValueError(f"ключей {n}, значений {n2}")
    return list(zip(keys, values))
