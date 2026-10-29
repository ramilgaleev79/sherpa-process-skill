"""DataTable-контейнер: XmlSchema + XmlDiffGram (golden b11).

Формула подтверждена побайтово на трёх состояниях (пустая/schema-only/с данными):

    HEADER(17) + 0C + id=2 + bw("System.Data, Version=4.0.0.0, …")
    + 05 + id=1 + bw("System.Data.DataTable") + memberCount=3
    + bw("DataTable.RemotingVersion") + bw("XmlSchema") + bw("XmlDiffGram")
    + 03 01 01                        (member enums)
    + 0E + bw("System.Version") + 02 00 00 00 + 09 + 03 00 00 00
    + 06 + id=4 + bw7(len) + XmlSchema        (UTF-8, CRLF внутри)
    + 06 + id=5 + bw7(len) + XmlDiffGram
    + 04 + id=3 + 0E + bw("System.Version") + memberCount=4
    + bw("_Major")…bw("_Revision") + 00 00 00 00 + 08 08 08 08
    + RemotingVersion = 2.0.0.-1 (константа)
    + 0B                              (терминатор, как у всех контейнеров)

Сами XML-строки (schema/diffgram) — их содержимое определяется сериализатором
таблицы (build_schema/build_diffgram, см. ниже); переносы — CRLF.
"""

from __future__ import annotations

import base64

from .text import decode_7bit

HEADER = bytes.fromhex("0001000000ffffffff0100000000000000")
ASSEMBLY = "System.Data, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"


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


def encode(schema_xml: str, diffgram_xml: str) -> str:
    """XmlSchema и XmlDiffGram (готовые XML-строки с CRLF) → Base64-контейнер."""
    schema = schema_xml.encode("utf-8")
    diffgram = diffgram_xml.encode("utf-8")
    out = bytearray(HEADER)
    out += b"\x0c" + _i32(2) + _bws(ASSEMBLY)
    out += b"\x05" + _i32(1) + _bws("System.Data.DataTable")
    out += _i32(3)
    out += _bws("DataTable.RemotingVersion") + _bws("XmlSchema") + _bws("XmlDiffGram")
    out += bytes.fromhex("030101")
    out += _bws("System.Version") + _i32(2) + b"\x09" + _i32(3)
    out += b"\x06" + _i32(4) + _bw7(len(schema)) + schema
    out += b"\x06" + _i32(5) + _bw7(len(diffgram)) + diffgram
    out += b"\x04" + _i32(3) + _bws("System.Version") + _i32(4)
    out += _bws("_Major") + _bws("_Minor") + _bws("_Build") + _bws("_Revision")
    out += bytes.fromhex("0000000008080808")
    out += _i32(2) + _i32(0) + _i32(-1) + _i32(-1)  # RemotingVersion 2.0.0.-1
    out += b"\x0b"
    return base64.b64encode(bytes(out)).decode("ascii")


def decode(container: str) -> tuple[str, str]:
    """Base64-контейнер → (XmlSchema, XmlDiffGram). Строгие якоря по формуле."""
    raw = base64.b64decode(container, validate=True)
    if not raw.startswith(HEADER + b"\x0c"):
        raise ValueError("это не DataTable-контейнер")

    def read_object_string(marker_id: int) -> tuple[str, int]:
        anchor = b"\x06" + _i32(marker_id)
        idx = raw.find(anchor)
        if idx < 0:
            raise ValueError(f"не найдена XML-вставка id={marker_id}")
        pos = idx + len(anchor)
        length, pos = decode_7bit(raw, pos)
        end = pos + length
        if end > len(raw):
            raise ValueError("XML-вставка выходит за границы")
        return raw[pos:end].decode("utf-8"), end

    schema, _ = read_object_string(4)
    diffgram, _ = read_object_string(5)
    if not raw.endswith(b"\x0b"):
        raise ValueError("нет терминатора 0x0B")
    return schema, diffgram


# --- Строители XML-частей (формат golden b11, ширина отступов и CRLF — норматив) ---

SCHEMA_HEADER = (
    '<?xml version="1.0" encoding="utf-16"?>\r\n'
    '<xs:schema xmlns="" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:msdata="urn:schemas-microsoft-com:xml-msdata">\r\n'
    '  <xs:element name="Table1">\r\n'
    '    <xs:complexType>\r\n'
)
SCHEMA_EMPTY_BODY = ""
SCHEMA_COLUMNS_BODY = "      <xs:sequence>\r\n"
SCHEMA_FOOTER = (
    "    </xs:complexType>\r\n"
    "  </xs:element>\r\n"
    '  <xs:element name="tmpDataSet" msdata:IsDataSet="true" msdata:MainDataTable="Table1" '
    'msdata:UseCurrentLocale="true">\r\n'
    "    <xs:complexType>\r\n"
    '      <xs:choice minOccurs="0" maxOccurs="unbounded" />\r\n'
    "    </xs:complexType>\r\n"
    "  </xs:element>\r\n"
    "</xs:schema>"
)

# Подтверждённые типы колонок (§14.5): имя_типа → (xsd_type, msdata_DataType|None)
COLUMN_TYPES: dict[str, tuple[str, str | None]] = {
    "String": ("xs:string", None),
    "Int32": ("xs:int", None),
    "Double": ("xs:double", None),
    "Decimal": ("xs:decimal", None),
    "DateTime": ("xs:dateTime", None),
    "Boolean": ("xs:boolean", None),
    "Guid": ("xs:string", "System.Guid, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"),
}

DIFFGRAM_EMPTY = (
    '<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
    'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1" />'
)


def build_schema(columns: list[tuple[str, str]] | None) -> str:
    """Схема по списку колонок [(имя, тип_из_COLUMN_TYPES)]; None/[] — пустая таблица."""
    if not columns:
        return SCHEMA_HEADER + SCHEMA_EMPTY_BODY + SCHEMA_FOOTER
    lines = [SCHEMA_HEADER + SCHEMA_COLUMNS_BODY]
    for name, col_type in columns:
        if col_type not in COLUMN_TYPES:
            raise ValueError(f"тип колонки {col_type!r} не подтверждён (§14.5)")
        xsd, msdata_type = COLUMN_TYPES[col_type]
        # golden: у Guid msdata:DataType стоит ПЕРЕД type (порядок атрибутов нормативен)
        extra = f'msdata:DataType="{msdata_type}" ' if msdata_type else ""
        lines.append(
            f'        <xs:element name="{_xml_escape(name)}" {extra}type="{xsd}" '
            'msdata:targetNamespace="" minOccurs="0" />\r\n'
        )
    lines.append("      </xs:sequence>\r\n")
    lines.append(SCHEMA_FOOTER)
    return "".join(lines)


def build_diffgram(rows: list[dict[str, str]] | None) -> str:
    """DiffGram по строкам-словарям {колонка: значение}; None/[] — пустой diffgram.

    Подтверждённые правила (§14.7–14.8): row id Table1{N} с 1, rowOrder с 0,
    hasChanges="inserted", DBNull = отсутствие элемента ячейки (пропущенный ключ).
    """
    if not rows:
        return DIFFGRAM_EMPTY
    out = [
        '<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
        'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">\r\n'
        "  <tmpDataSet>\r\n"
    ]
    for order, row in enumerate(rows, start=1):
        out.append(
            f'    <Table1 diffgr:id="Table1{order}" msdata:rowOrder="{order - 1}" '
            'diffgr:hasChanges="inserted">\r\n'
        )
        for col, value in row.items():
            if not isinstance(col, str) or not isinstance(value, str):
                raise ValueError(
                    "DataTable: значения ячеек должны быть строками; "
                    "DBNull кодируется отсутствием ключа"
                )
            out.append(f"      <{_xml_escape(col)}>{_xml_escape(value)}</{_xml_escape(col)}>\r\n")
        out.append("    </Table1>\r\n")
    out.append("  </tmpDataSet>\r\n</diffgr:diffgram>")
    return "".join(out)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
