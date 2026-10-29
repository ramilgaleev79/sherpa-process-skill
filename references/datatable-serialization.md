# Сериализация DataTable в .process (подтверждено golden)

Источник истины: `golden/real/b11-datatables-x3.process` (пустая таблица / schema-only / схема+строки), дополнительно `b10-datatable-ops.process` и `diag11-log174-datatable.process`.

## Модель контейнера

```
DiagramProperty @DefaultValue = Base64( контейнер )
контейнер = общий заголовок (17 байт, см. text-serialization.md)
          + 06 + 02 00 00 00 + 0C + 7bit-len + "System.Data, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
          + XmlSchema (XML-строка с declaration `<?xml version="1.0" encoding="utf-16"?>`)
          + XmlDiffGram (XML-строка)
          + хвост: сериализация System.Version (_Major _Minor _Build _Revision)
```

Побайтовая раскладка префиксов/длин между вставками — фаза 3 (образцы сняты, hex в логах инспектора).

## XmlSchema

Три подтверждённых состояния:

1. **Пустая таблица** (930 Б): `xs:element name="Table1"` с пустым `xs:complexType`.
2. **Schema-only** (1254 Б): `xs:sequence` с колонками.
3. **Схема+строки** (1829 Б).

Каркас (константен):

```xml
<?xml version="1.0" encoding="utf-16"?>
<xs:schema xmlns="" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:msdata="urn:schemas-microsoft-com:xml-msdata">
  <xs:element name="Table1">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="colN" type="T" msdata:targetNamespace="" minOccurs="0" />
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="tmpDataSet" msdata:IsDataSet="true" msdata:MainDataTable="Table1" msdata:UseCurrentLocale="true">
    <xs:complexType>
      <xs:choice minOccurs="0" maxOccurs="unbounded" />
    </xs:complexType>
  </xs:element>
</xs:schema>
```

Подтверждённые типы колонок: `xs:string`, `xs:double`, `xs:dateTime`, `xs:int`, `xs:decimal`; Guid — `xs:string` + атрибут `msdata:DataType="System.Guid, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"`.

## XmlDiffGram

- Пустая таблица / schema-only: самозакрытый `<diffgr:diffgram xmlns:msdata=… xmlns:diffgr=… />`.
- Со строками:

```xml
<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">
  <tmpDataSet>
    <Table1 diffgr:id="Table11" msdata:rowOrder="0" diffgr:hasChanges="inserted">
      <col1>1</col1>
      <col2>2</col2>
      <col3>3</col3>
    </Table1>
    <Table1 diffgr:id="Table12" msdata:rowOrder="1" diffgr:hasChanges="inserted">…</Table1>
  </tmpDataSet>
</diffgr:diffgram>
```

Подтверждены все параметры §14.8: `diffgr:id="Table1{N}"` с N от 1, `msdata:rowOrder` с 0, `hasChanges="inserted"`, dataset `tmpDataSet`, таблица `Table1`.

## Null-политика (§14.6 подтверждён)

Колонка col4 (Guid) объявлена в схеме, но в строках table3 её элементы отсутствуют — DBNull кодируется опущением элемента ячейки. Пустая строка ≠ null.

## Побайтовая раскладка контейнера — РЕАЛИЗОВАНА (фаза 3.2)

Полная формула в `scripts/sherpa_xml/types/data_table.py` (docstring модуля);
round-trip байт-в-байт подтверждён тестами на всех трёх состояниях golden (b11),
builder'ы `build_schema`/`build_diffgram` воспроизводят golden-XML точно
(включая CRLF и порядок атрибутов: у Guid-колонок `msdata:DataType` перед `type`).
Хвост контейнера — сериализация `System.Version` со значением RemotingVersion
`2.0.0.-1` (константа). Аналогично реализованы List и Hashtable
(`types/collections.py`), byte-equal против b09/b07. Dictionary — encode/decode
отложен (вложенные KeyValuePair`2-записи, нужен второй golden-образец);
single-sample риски (capacity List, Version/HashSize Hashtable) помечены
в docstring и снимаются новыми образцами.

## Осталось

- Значения ячеек: форматы дат (ISO 8601 + offset), Guid (канонический), Boolean;
  значения принимаются строками и экранируются XML-билдером, типизация значений — фаза 4/5.
  `null` запрещён: DBNull кодируется отсутствием ключа.
- Вложенные Hashtable/DataTable в ячейках — не подтверждено (за пределами MVP, §14.9).
