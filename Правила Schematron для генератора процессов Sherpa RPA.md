# Правила Schematron для генератора процессов Sherpa RPA

## Принцип применения

Schematron в этом скиле должен проверять не XML-синтаксис, а **семантические инварианты процесса**: обязательные узлы, согласованность ссылок, допустимость портов, достижимость, свойства блоков, переменные, подпроцессы и детерминированные поля. Структуру элементов, типы простых значений и порядок дочерних элементов следует оставить XSD или профильному валидатору; Schematron предназначен для правил, выражаемых как XPath assertions над несколькими частями документа.[^1]

Пока точная XML-модель `.process` не извлечена из эталонов, нельзя корректно зафиксировать реальные XPath Sherpa. Поэтому сейчас следует утвердить **каталог логических правил и контракт отчёта**, а после загрузки эталонов заменить абстрактные XPath на подтверждённые пути профиля конкретной версии Designer.

## Место в конвейере

```text
Process Specification
  → валидация входной схемы
  → Process IR
  → валидация графа в Python
  → генерация .process XML
  → well-formed XML
  → inferred XSD профиля Sherpa
  → Schematron
  → golden/round-trip проверка
  → выпуск артефакта
```

Schematron не должен быть единственным валидатором. Проверки вычислительно сложного графа — например, полной достижимости в произвольном циклическом графе — надёжнее дублировать в Python; Schematron остаётся проверяемой декларативной спецификацией для локальных и ссылочных инвариантов.

## Фазы Schematron

Фазы позволяют запускать именованные наборы patterns; `lxml.isoschematron` поддерживает выбор фазы при компиляции валидатора. Для скила рекомендуется четыре фазы.[^2]

| Фаза | Назначение | Когда запускать |
|---|---|---|
| `structural` | Корень, обязательные контейнеры, уникальность ID, целостность ссылок | Всегда |
| `semantic` | Start/End, порты, блоки, переменные, подпроцессы | Всегда после `structural` |
| `deterministic` | Стабильные ID, запрещённые volatile-поля, порядок и нормализация | При генерации и regression tests |
| `strict` | Все предыдущие patterns и правила без предупреждений | Перед выдачей итогового `.process` |

Для MVP обязательной является `strict`. Отдельные фазы нужны разработчику, чтобы быстро локализовать класс ошибки, но не должны позволять выпустить файл с частичной проверкой.

## Формат правила

Каждое правило получает неизменяемый идентификатор:

```text
SHP-<область>-<номер>
```

Области:

- `DOC` — документ и профиль;
- `ID` — идентификаторы и ссылки;
- `GRF` — граф;
- `BLK` — блоки и порты;
- `PRP` — свойства;
- `VAR` — переменные и выражения;
- `SUB` — подпроцессы;
- `DET` — детерминизм;
- `SEC` — безопасность XML.

У каждого `sch:assert` обязательны:

- `id` — стабильный код правила;
- `role` — `FATAL`, `ERROR` или `WARN`;
- `diagnostics` — ссылка на диагностическое сообщение;
- текст с ожидаемым инвариантом;
- максимально узкий `context`;
- XPath в `test`, не зависящий от порядка там, где порядок не является частью формата.

SVRL обычно предоставляет `id`, `location`, `test` и `role`, что позволяет преобразовать результат в машинный отчёт без разбора свободного текста.[^3]

## Уровни ошибок

| Role | Значение | Разрешён выпуск XML |
|---|---|---|
| `FATAL` | Невозможно надёжно продолжить проверку: неизвестный профиль, повреждённый корень, неоднозначные ID | Нет |
| `ERROR` | Нарушен обязательный инвариант процесса | Нет |
| `WARN` | Формат допустим, но обнаружено подозрительное или непредпочтительное состояние | Только вне `strict` |

В `strict` любой `failed-assert` с `FATAL`, `ERROR` или `WARN` завершает генерацию. Это требует собственной фильтрации SVRL, поскольку стандартное поведение `lxml.isoschematron` ориентировано на `failed-assert`, а правила обработки отчётов и уровней можно настроить через `error_finder` или разбор сохранённого `validation_report`.[^2]

## Каталог правил MVP

### Документ и профиль

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-DOC-001` | FATAL | Документ | Корневой элемент соответствует профилю `.process` |
| `SHP-DOC-002` | FATAL | Документ | Версия/формат документа входит в allowlist активного профиля |
| `SHP-DOC-003` | ERROR | Корень | Присутствуют все обязательные контейнеры профиля |
| `SHP-DOC-004` | ERROR | Документ | Отсутствуют неизвестные элементы в закрытых областях модели |
| `SHP-DOC-005` | ERROR | Документ | Отсутствуют неизвестные атрибуты в закрытых областях модели |
| `SHP-DOC-006` | ERROR | Корень | Объявлены требуемые namespace URI |

`DOC-004` и `DOC-005` следует включать только для областей, которые эталоны подтвердят как закрытые. Для extension-контейнеров применяется allowlist namespace, иначе будущие версии Designer будут ошибочно отклоняться.

### Идентификаторы и ссылки

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-ID-001` | FATAL | Каждый объект с ID | ID непустой после нормализации |
| `SHP-ID-002` | FATAL | Корень | Все объектные ID уникальны в своей области |
| `SHP-ID-003` | ERROR | Каждая ссылка | Целевой ID существует |
| `SHP-ID-004` | ERROR | Каждая связь | Исходный node/port существует |
| `SHP-ID-005` | ERROR | Каждая связь | Целевой node/port существует |
| `SHP-ID-006` | ERROR | Каждая ссылка переменной | Переменная существует в допустимой области видимости |
| `SHP-ID-007` | ERROR | Каждый вызов процесса | Ссылка на диаграмму непустая и нормализована |
| `SHP-ID-008` | ERROR | Корень | Один ID не используется объектами несовместимых типов, если профиль требует глобальную уникальность |

### Граф процесса

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-GRF-001` | ERROR | Диаграмма | Ровно один блок Start |
| `SHP-GRF-002` | ERROR | Диаграмма | Присутствует минимум один блок End |
| `SHP-GRF-003` | ERROR | Start | Нет входящих управляющих связей |
| `SHP-GRF-004` | ERROR | Start | Есть ровно одна исходящая связь через разрешённый порт |
| `SHP-GRF-005` | ERROR | End | Нет исходящих управляющих связей |
| `SHP-GRF-006` | ERROR | Обычный исполняемый блок | Есть минимум одна входящая управляющая связь |
| `SHP-GRF-007` | ERROR | Обязательный выход блока | Есть требуемая исходящая связь |
| `SHP-GRF-008` | ERROR | Связь | Запрещена связь узла с самим собой, кроме типов из allowlist |
| `SHP-GRF-009` | ERROR | Пара узел–порт | Не превышено допустимое число исходящих связей |
| `SHP-GRF-010` | ERROR | Диаграмма | Все блоки достижимы от Start |
| `SHP-GRF-011` | ERROR | Диаграмма | Из каждого исполняемого блока существует путь к End либо к разрешённому терминальному блоку |
| `SHP-GRF-012` | ERROR | Диаграмма | Нет дублирующих связей с одинаковыми source, source-port, target и target-port |

Официальные примеры Sherpa описывают Start как начало любой диаграммы, End — как завершение сценария или возврат из подпроцесса, а блок «Процесс» — как переход в другую диаграмму с возвратом после её End. Точные ограничения по количеству Start/End и портам всё равно должны быть подтверждены эталонными `.process` для выбранной версии.[^4]

`GRF-010` и `GRF-011` трудно реализовать переносимо на XPath 1.0. Для MVP они выполняются Python-валидатором как нормативные правила, а в Schematron добавляются только после выбора процессора с XPath 2.0/3.1 или после введения заранее вычисленных метаданных достижимости.

### Блоки и порты

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-BLK-001` | ERROR | Каждый блок | Тип блока присутствует в `block-catalog` профиля |
| `SHP-BLK-002` | ERROR | Каждый блок | Набор входных портов соответствует типу блока |
| `SHP-BLK-003` | ERROR | Каждый блок | Набор выходных портов соответствует типу блока |
| `SHP-BLK-004` | ERROR | Каждый порт | Имя/тип порта разрешён для блока |
| `SHP-BLK-005` | ERROR | Условие | Присутствуют все подтверждённые ветви, например true/false |
| `SHP-BLK-006` | ERROR | Цикл | Присутствуют порты тела и завершения цикла |
| `SHP-BLK-007` | ERROR | Блок ошибки | Error-выход используется только если он поддерживается типом блока |
| `SHP-BLK-008` | ERROR | Каждый блок | Внутренний XML-класс блока соответствует его логическому типу |

Набор допустимых типов нельзя хранить только внутри `.sch`. Он должен генерироваться из версионированного `block-catalog.yaml`, чтобы компилятор и Schematron использовали один источник истины.

### Свойства блоков

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-PRP-001` | ERROR | Каждый блок | Все обязательные свойства типа присутствуют |
| `SHP-PRP-002` | ERROR | Каждый блок | Неизвестные свойства отсутствуют |
| `SHP-PRP-003` | ERROR | Каждое свойство | Представление значения соответствует типу: constant, variable или expression |
| `SHP-PRP-004` | ERROR | Каждое свойство | Пустое значение допустимо для данного свойства |
| `SHP-PRP-005` | ERROR | Числовое свойство | Значение входит в допустимый диапазон |
| `SHP-PRP-006` | ERROR | Enum-свойство | Значение входит в allowlist профиля |
| `SHP-PRP-007` | ERROR | Условно обязательное свойство | Свойство присутствует при выполнении условия другого свойства |
| `SHP-PRP-008` | ERROR | Взаимоисключающие свойства | Указан только один разрешённый вариант |
| `SHP-PRP-009` | ERROR | Путь/имя файла | Формат и относительность пути соответствуют политике проекта |
| `SHP-PRP-010` | WARN | Текстовое свойство | Отсутствуют лишние начальные/конечные пробелы, если они незначимы |

Sherpa различает константы, переменные и выражения в свойствах, причём не все свойства принимают выражения. Поэтому `PRP-003` должен использовать матрицу возможностей конкретного свойства, а не общий регулярный шаблон для всех значений.[^5]

### Переменные

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-VAR-001` | ERROR | Диаграмма/проект | Имена переменных уникальны в области видимости |
| `SHP-VAR-002` | ERROR | Переменная | Имя соответствует подтверждённому формату Sherpa |
| `SHP-VAR-003` | ERROR | Переменная | Тип входит в allowlist профиля |
| `SHP-VAR-004` | ERROR | Переменная | Значение по умолчанию совместимо с типом |
| `SHP-VAR-005` | ERROR | Ссылка на переменную | Ссылка начинается и кодируется по правилам свойства |
| `SHP-VAR-006` | ERROR | Локальная переменная | Использование не выходит за область диаграммы |
| `SHP-VAR-007` | ERROR | In/Out-аргумент | Направление и тип согласованы между вызовом и подпроцессом |
| `SHP-VAR-008` | WARN | Переменная | Переменная используется хотя бы один раз, если не помечена как резервная |

Правила локальных переменных и In/Out-аргументов должны включаться только в профилях версий, где эта функция подтверждена. В Sherpa Designer 1.25 была добавлена поддержка локальных переменных и аргументов подпроцессов, а старые проекты по умолчанию открываются в режиме глобальных переменных.[^6]

### Подпроцессы

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-SUB-001` | ERROR | Блок Process | Имя/путь вызываемой диаграммы присутствует |
| `SHP-SUB-002` | ERROR | Блок Process | Диаграмма не вызывает саму себя, если рекурсия не разрешена профилем |
| `SHP-SUB-003` | ERROR | Аргумент вызова | Имя аргумента существует в контракте подпроцесса |
| `SHP-SUB-004` | ERROR | Аргумент вызова | Каждый обязательный In-аргумент передан |
| `SHP-SUB-005` | ERROR | Аргумент вызова | Тип переданного выражения совместим с типом аргумента |
| `SHP-SUB-006` | ERROR | Out-аргумент | Целевая переменная существует и доступна для записи |
| `SHP-SUB-007` | ERROR | Проект | Все ссылки на локальные `.process` разрешаются относительно `.sherpa` |

`SUB-003`–`SUB-007` требуют проверки нескольких файлов. Однодокументный Schematron следует использовать только если процессор разрешает безопасный `document()` и все пути заранее ограничены корнем проекта; более безопасный вариант — собрать временный validation bundle XML со сведениями обо всех диаграммах.

### Детерминизм

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-DET-001` | ERROR | Документ | Отсутствуют текущие timestamp, если поле не является обязательным |
| `SHP-DET-002` | ERROR | Документ | Отсутствуют абсолютные пути рабочей машины |
| `SHP-DET-003` | ERROR | Документ | Отсутствуют имя пользователя, имя хоста и временные каталоги |
| `SHP-DET-004` | ERROR | Каждый ID | ID соответствует выбранной детерминированной политике |
| `SHP-DET-005` | ERROR | Коллекция | Элементы следуют нормативному порядку профиля |
| `SHP-DET-006` | ERROR | Число | Использован единый инвариантный формат числа |
| `SHP-DET-007` | ERROR | Булево значение | Использовано одно нормативное представление |
| `SHP-DET-008` | ERROR | Пустое значение | Используется подтверждённый вариант: отсутствие, пустой элемент или атрибут |
| `SHP-DET-009` | ERROR | Строка | Переносы строк нормализованы по политике профиля |
| `SHP-DET-010` | WARN | Layout | Координаты соответствуют фиксированной сетке генератора |

`DET-004` требует доступной в XPath функции вычисления или проверки ID. Если используется UUIDv5, Schematron проверяет формат и, при наличии расширения процессора, вычисленное значение; полное совпадение `key → UUIDv5` обязательно проверяет Python.

### Безопасность

| ID | Role | Логический context | Assert |
|---|---|---|---|
| `SHP-SEC-001` | FATAL | Parser | DTD запрещён |
| `SHP-SEC-002` | FATAL | Parser | Внешние сущности и сетевой доступ запрещены |
| `SHP-SEC-003` | ERROR | Script/expression | Значение не содержит запрещённых конструкций политики скила |
| `SHP-SEC-004` | ERROR | Путь | Выход за корень проекта через `..` запрещён |
| `SHP-SEC-005` | ERROR | Секреты | XML не содержит полей, классифицированных как секретные |

`SEC-001` и `SEC-002` реализуются настройками XML-парсера до Schematron, поскольку DTD и сущности обрабатываются раньше XPath-проверок. Schematron здесь фиксирует нормативное требование, а не заменяет безопасный парсинг.

## Каркас `.sch`

Ниже приведён исполняемый каркас **для условной нормализованной XML-модели**, а не для реального `.process`. Префикс `p` и пути будут заменены после анализа эталонов Sherpa.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron"
            xmlns:p="urn:sherpa-skill:process-ir"
            queryBinding="xslt">

  <sch:title>Sherpa process invariants</sch:title>
  <sch:ns prefix="p" uri="urn:sherpa-skill:process-ir"/>

  <sch:phase id="structural">
    <sch:active pattern="document"/>
    <sch:active pattern="identifiers"/>
    <sch:active pattern="references"/>
  </sch:phase>

  <sch:phase id="semantic">
    <sch:active pattern="graph"/>
    <sch:active pattern="blocks"/>
    <sch:active pattern="variables"/>
  </sch:phase>

  <sch:phase id="deterministic">
    <sch:active pattern="determinism"/>
  </sch:phase>

  <sch:phase id="strict">
    <sch:active pattern="document"/>
    <sch:active pattern="identifiers"/>
    <sch:active pattern="references"/>
    <sch:active pattern="graph"/>
    <sch:active pattern="blocks"/>
    <sch:active pattern="variables"/>
    <sch:active pattern="determinism"/>
  </sch:phase>

  <sch:pattern id="document">
    <sch:rule context="/">
      <sch:assert id="SHP-DOC-001" role="FATAL"
                  test="p:process"
                  diagnostics="D-SHP-DOC-001">
        Корневой элемент должен быть process активного профиля.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="identifiers">
    <sch:rule context="p:process">
      <sch:assert id="SHP-ID-002" role="FATAL"
                  test="count(.//*[@id]) = count(.//*[@id][not(@id = preceding::*/@id)])"
                  diagnostics="D-SHP-ID-002">
        Все объектные ID должны быть уникальны.
      </sch:assert>
    </sch:rule>
    <sch:rule context="*[@id]">
      <sch:assert id="SHP-ID-001" role="FATAL"
                  test="normalize-space(@id) != ''"
                  diagnostics="D-SHP-ID-001">
        ID объекта не должен быть пустым.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="references">
    <sch:rule context="p:edge">
      <sch:assert id="SHP-ID-004" role="ERROR"
                  test="count(/p:process/p:nodes/p:node[@id = current()/@source]) = 1"
                  diagnostics="D-SHP-ID-004">
        Исходный узел связи должен существовать ровно один раз.
      </sch:assert>
      <sch:assert id="SHP-ID-005" role="ERROR"
                  test="count(/p:process/p:nodes/p:node[@id = current()/@target]) = 1"
                  diagnostics="D-SHP-ID-005">
        Целевой узел связи должен существовать ровно один раз.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="graph">
    <sch:rule context="p:process">
      <sch:assert id="SHP-GRF-001" role="ERROR"
                  test="count(p:nodes/p:node[@type='Start']) = 1"
                  diagnostics="D-SHP-GRF-001">
        Диаграмма должна содержать ровно один Start.
      </sch:assert>
      <sch:assert id="SHP-GRF-002" role="ERROR"
                  test="count(p:nodes/p:node[@type='End']) &gt;= 1"
                  diagnostics="D-SHP-GRF-002">
        Диаграмма должна содержать минимум один End.
      </sch:assert>
    </sch:rule>

    <sch:rule context="p:node[@type='Start']">
      <sch:assert id="SHP-GRF-003" role="ERROR"
                  test="count(/p:process/p:edges/p:edge[@target = current()/@id]) = 0">
        Start не должен иметь входящих управляющих связей.
      </sch:assert>
      <sch:assert id="SHP-GRF-004" role="ERROR"
                  test="count(/p:process/p:edges/p:edge[@source = current()/@id]) = 1">
        Start должен иметь ровно одну исходящую связь.
      </sch:assert>
    </sch:rule>

    <sch:rule context="p:node[@type='End']">
      <sch:assert id="SHP-GRF-005" role="ERROR"
                  test="count(/p:process/p:edges/p:edge[@source = current()/@id]) = 0">
        End не должен иметь исходящих управляющих связей.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="blocks">
    <sch:rule context="p:node">
      <sch:assert id="SHP-BLK-001" role="ERROR"
                  test="@type='Start' or @type='End' or @type='Process'">
        Тип блока должен входить в allowlist активного профиля.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="variables">
    <sch:rule context="p:process/p:variables">
      <sch:assert id="SHP-VAR-001" role="ERROR"
                  test="count(p:variable) = count(p:variable[not(@name = preceding-sibling::p:variable/@name)])">
        Имена переменных должны быть уникальны в области видимости.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:pattern id="determinism">
    <sch:rule context="p:process">
      <sch:assert id="SHP-DET-002" role="ERROR"
                  test="not(.//@*[contains(., ':\') or starts-with(., '/')])">
        Документ не должен содержать абсолютные локальные пути.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <sch:diagnostics>
    <sch:diagnostic id="D-SHP-DOC-001">Проверьте профиль и корневой элемент документа.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-ID-001">Сгенерируйте ID по детерминированной политике.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-ID-002">Найдены повторяющиеся объектные ID.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-ID-004">Поле source ссылается на отсутствующий узел.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-ID-005">Поле target ссылается на отсутствующий узел.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-GRF-001">Добавьте единственный стартовый блок.</sch:diagnostic>
    <sch:diagnostic id="D-SHP-GRF-002">Добавьте хотя бы один завершающий блок.</sch:diagnostic>
  </sch:diagnostics>
</sch:schema>
```

При использовании XSLT 1.0 необходимо проверить поддержку `current()` конкретным Schematron pipeline. `lxml.isoschematron` компилирует схему через XSLT 1.0 skeleton и позволяет сохранить скомпилированный XSLT и SVRL-отчёт для диагностики.[^7][^2]

## Python-интеграция

```python
from copy import deepcopy
from lxml import etree
from lxml.isoschematron import Schematron

SVRL = {"svrl": "http://purl.oclc.org/dsdl/svrl"}


def secure_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        remove_blank_text=False,
        huge_tree=False,
    )


def validate_schematron(xml_path: str, sch_path: str, phase: str = "strict") -> dict:
    parser = secure_parser()
    xml_doc = etree.parse(xml_path, parser)
    sch_doc = etree.parse(sch_path, parser)

    validator = Schematron(
        sch_doc,
        phase=phase,
        store_report=True,
        validate_schema=True,
    )
    validator.validate(xml_doc)

    report = deepcopy(validator.validation_report)
    issues = []
    for item in report.xpath("//svrl:failed-assert", namespaces=SVRL):
        text = " ".join(
            "".join(item.xpath("svrl:text//text()", namespaces=SVRL)).split()
        )
        issues.append({
            "code": item.get("id"),
            "severity": item.get("role", "ERROR"),
            "location": item.get("location"),
            "test": item.get("test"),
            "message": text,
        })

    blocking = {"FATAL", "ERROR", "WARN"} if phase == "strict" else {"FATAL", "ERROR"}
    return {
        "status": "validation_failed"
        if any(x["severity"] in blocking for x in issues)
        else "passed",
        "phase": phase,
        "issues": issues,
    }
```

Сохранение `validation_report` даёт доступ к SVRL после проверки. Полный SVRL следует сохранять как диагностический артефакт, а пользователю возвращать нормализованный JSON/YAML с кодами правил.[^2]

## Генерация правил из каталога

Для десятков типов блоков ручное копирование `sch:rule` станет источником рассинхронизации. Источником истины должен стать каталог:

```yaml
blocks:
  Start:
    inputs: []
    outputs:
      default: {min: 1, max: 1}
    required_properties: []

  End:
    inputs:
      input: {min: 1}
    outputs: {}
    required_properties: []

  Process:
    inputs:
      input: {min: 1}
    outputs:
      success: {min: 1, max: 1}
      error: {min: 0, max: 1}
    required_properties:
      - process_name
```

Из этого YAML сборочный скрипт должен создавать:

- allowlist типов в `SHP-BLK-001`;
- правила обязательных свойств;
- правила допустимых портов;
- ограничения количества связей;
- справочник кодов ошибок;
- тестовые XML-фрагменты для каждого правила.

Сгенерированный `.sch` коммитится рядом с каталогом, но не редактируется вручную. CI проверяет, что повторная генерация не изменяет файл.

## Проверка самих правил

Для каждого assert необходима минимум одна валидная и одна невалидная фикстура. Тест должен подтвердить не только общий `false`, но и точный `id`, `role` и ожидаемую `location` в SVRL.

```text
tests/schematron/
├── SHP-GRF-001/
│   ├── valid-one-start.xml
│   ├── invalid-no-start.xml
│   └── invalid-two-starts.xml
├── SHP-ID-003/
│   ├── valid-reference.xml
│   └── invalid-dangling-reference.xml
└── SHP-PRP-007/
    ├── valid-condition.xml
    └── invalid-missing-dependent-property.xml
```

Mutation tests должны удалять обязательный атрибут, дублировать ID, менять target, подставлять неизвестный порт, убирать ветвь и добавлять volatile-поле. Если мутация не вызывает заранее определённый код правила, тест считается проваленным.

## Порядок внедрения

1. Утвердить коды правил и уровни ошибок из этого документа.
2. Создать Schematron для нейтральной Process IR и проверить архитектуру отчётов.
3. Получить первую серию `.process` из одной версии Designer.
4. Построить карту `логическая сущность → реальный XPath Sherpa`.
5. Создать `namespaces.sch.inc` и профильный `.sch`.
6. Реализовать `DOC`, `ID` и простые `GRF`-правила.
7. Добавить каталог блоков и генерируемые `BLK`/`PRP`-правила.
8. Добавить переменные и подпроцессы после появления эталонов.
9. Реализовать Python-проверки достижимости и сверять их результат с SVRL.
10. Включить фазу `strict` в обязательный release gate.

## Данные для следующего шага

Чтобы превратить каталог в рабочий Schematron для Sherpa, первым пакетом достаточно передать:

- `.process` с `Start → End`;
- тот же процесс с одним промежуточным блоком;
- вариант с двумя одинаковыми блоками;
- вариант с ветвлением;
- вариант с одной переменной;
- точную версию Sherpa Designer;
- описание единственного изменения между каждой парой файлов.

После этого можно точно определить XPath для блоков, ID, портов, связей и переменных и выпустить первую профильную схему без предположений о внутреннем XML Sherpa.

---

## References

1. [ISO/IEC 19757-3:2020](https://www.iso.org/standard/74515.html) - Information technology — Document Schema Definition Languages (DSDL) — Part 3: Rule-based validation...

2. [lxml.isoschematron package — lxml documentation](https://lxml.de/apidoc/lxml.isoschematron.html)

3. [Schematron files](https://docs.ted.europa.eu/eforms/1.14/schematrons/index.html)

4. [1С Приходная накладная (COM) | SherpaRPA](https://docs.sherparpa.ru/sherpa-rpa/sherpa-designer/primery-robotov/1s-prikhodnaya-nakladnaya-com)

5. [Панель свойств · SherpaBook - База знаний - Sherpa RPA](https://docs.sherparpa.ru/sherpa-rpa/sherpa-designer/rabota-v-sherpa-designer/paneli-svoistv-i-peremennykh-i-rabota-s-nimi/panel-svoistv) - Свойства блоков в их большинстве относятся к трем категориям: Основные свойства – предназначены для ...

6. [Version 1.25 · SherpaBook - Sherpa RPA](https://docs.sherparpa.ru/En/obnovleniya/obnovleniya-sherpa-designer/arkhiv-obnovlenii/versiya-1.25)

7. [Validation with lxml](https://lxml.de/validation.html)

