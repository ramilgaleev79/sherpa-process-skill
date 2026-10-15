"""Text: PS-литерал + бинарный контейнер + Base64 (main.md §11, golden b05).

Формат подтверждён побайтово (references/text-serialization.md):

    Base64( PREFIX + encode_7bit(len(UTF8(literal))) + UTF8(literal) + 0x0B )

PREFIX — константа 22 байта (снята с golden, менять только с новым эталоном).

Правила PS-литерала:
- обёртка двойными кавычками;
- кавычка → `` `" `` (стиль writer'а; подтверждён golden TextQuotes/TextSimple/Ru/Empty);
- LF/CR → `` `n ``;
- backtick → `` `` `` (стандарт PS; golden-образцом не покрыт).

Чтение (ps_unliteral) толерантно к обоим стилям Designer: `` `" `` и
удвоение `""` (multiline-редактор Designer пишет второй стиль — golden
TextMultiline); голая одиночная кавычка внутри также принимается.
Writer всегда производит единый `` `" ``-стиль, поэтому byte-roundtrip
гарантирован для литералов, созданных нашим writer'ом, и для golden-литералов
`` `" ``-стиля; для `""`-стиля гарантируется семантический round-trip.
"""

from __future__ import annotations

import base64

PREFIX = bytes.fromhex("0001000000ffffffff01000000000000000601000000")  # 22 байта, golden b05/diag03
TERMINATOR = b"\x0b"


def ps_literal(raw: str) -> str:
    """Сырая строка → PowerShell-литерал Sherpa."""
    if not isinstance(raw, str):
        raise ValueError("ps_literal: ожидается сырая строка без PS-кавычек")
    out: list[str] = []
    for ch in raw:
        if ch == "`":
            out.append("``")
        elif ch == '"':
            out.append('`"')
        elif ch in "\n\r":
            out.append("`n")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def ps_unliteral(literal: str) -> str:
    """PowerShell-литерал Sherpa → сырая строка (для round-trip и валидатора).

    Толерантен к обоим стилям Designer: `` `" `` и удвоению `""`.
    """
    if len(literal) < 2 or literal[0] != '"' or literal[-1] != '"':
        raise ValueError(f"ps_unliteral: литерал должен быть в кавычках: {literal!r}")
    body = literal[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "`" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt in ("`", '"'):
                out.append(nxt)
            else:  # неизвестный escape — сохраняем как есть (writer такие не создаёт)
                out.append(ch + nxt)
            i += 2
        elif ch == '"' and i + 1 < len(body) and body[i + 1] == '"':
            out.append('"')  # удвоение "" → кавычка (стиль multiline-редактора Designer)
            i += 2
        elif ch == '"':
            out.append('"')  # голая одиночная кавычка внутри — толерантно
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def encode_7bit(n: int) -> bytes:
    """Length-prefixed 7-bit (LEB128-7bit, как BinaryWriter.Write7BitEncodedLength)."""
    if n < 0:
        raise ValueError("encode_7bit: отрицательная длина")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_7bit(data: bytes, offset: int) -> tuple[int, int]:
    """Читает 7-bit длину с offset; возвращает (длина, новый offset)."""
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("decode_7bit: данные закончились внутри длины")
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, offset
        shift += 7


def encode(raw: str) -> str:
    """Сырая строка → Base64-контейнер DiagramProperty."""
    literal = ps_literal(raw).encode("utf-8")
    payload = PREFIX + encode_7bit(len(literal)) + literal + TERMINATOR
    return base64.b64encode(payload).decode("ascii")


def decode(container: str) -> str:
    """Base64-контейнер → сырая строка; строгая проверка структуры (SEM-SER)."""
    try:
        raw = base64.b64decode(container, validate=True)
    except Exception as exc:
        raise ValueError(f"невалидный Base64: {exc}") from exc
    if not raw.startswith(PREFIX):
        raise ValueError("битый PREFIX текстового контейнера")
    if not raw.endswith(TERMINATOR):
        raise ValueError("нет терминатора 0x0B")
    body = raw[len(PREFIX):-1]
    length, offset = decode_7bit(body, 0)
    literal_bytes = body[offset:]
    if len(literal_bytes) != length:
        raise ValueError(f"длина не совпадает: заявлено {length}, фактических {len(literal_bytes)}")
    try:
        literal = literal_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"UTF-8 payload не читается: {exc}") from exc
    return ps_unliteral(literal)
