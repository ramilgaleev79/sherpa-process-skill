# sherpa-process

Скил ZCode: детерминированная генерация `.process` XML для **Sherpa RPA Designer 1.4.4**
из промежуточного представления (IR, JSON), с двухслойной валидацией и профилями
draft/release/strict. Политика confirmed-over-invented: каждый блок, тип и формат
сериализации подтверждён golden-эталоном (24 реальных файла Designer).

## Быстрый старт

```bash
python scripts/cli.py init-ir example.ir.json      # шаблон IR
# …редактируем example.ir.json…
python scripts/cli.py validate-ir example.ir.json --profile release
python scripts/cli.py build example.ir.json example.process --mode release
```

Открой `example.process` в Sherpa Designer 1.4.4 (лучше в тестовом проекте).

Требования: Python 3.10+, стандартная библиотека. Разработка/инструменты —
`.venv` (`python -m venv .venv && .venv/Scripts/pip install saxonche pyyaml pytest`).

## Команды CLI

| Команда | Назначение |
|---|---|
| `init-ir FILE` | шаблонный IR со всеми основными узлами |
| `validate-ir FILE [--profile …]` | проверка IR: контракт + SEM-*/SHP-* правила |
| `build IR OUT [--mode draft\|release\|strict]` | сборка .process: валидация профиля, компиляция, детерминизм-чек |

## Архитектура

```text
IR (JSON, агент) ──validate_ir──► отчёт (код/серьёзность/место)
   │
   └─build─► compiler: golden-шаблоны блоков (assets/block_templates.json)
             + uuid5-ID + layout + значения свойств + сериализация переменных
          ─► .process (UTF-8 BOM, CRLF — формат Designer)
          ─► validate_process + повторная компиляция (байт-в-байт)
```

- **IR-контракт** (`scripts/sherpa_xml/ir.py`) — закрытые множества: неизвестный
  тип узла/переменной/ключ — ошибка; ToDo требует `unresolved_action`.
- **Компилятор** (`compiler.py`) не конструирует XML блоков: берёт канонический
  golden-шаблон и подставляет значения; порты рёбер проверяются по шаблону.
- **Сериализаторы** (`types/`): Text (Base64-контейнер с 22-байтовым PREFIX),
  List/Hashtable (BinaryFormatter-подобные), DataTable (xs:schema+DiffGram) —
  все round-trip побайтово против golden (тесты).
- **Валидатор** (`validator.py`) — Python-слой (D3): граф, типы, выражения,
  контейнеры. Schematron (`schemas/sherpa-rules.sch`) — декларативная спецификация;
  прогон через SaxonC: `scripts/dev/run_schematron.py FILE --phase strict`.
- **Паттерны** (`patterns/`) — эталонные multi-block решения (retry, счётчик).
- **Конвейер расширения**: `scripts/dev/ingest_block.py` (новый блок из .process),
  `scripts/dev/extract_pattern.py` (новый паттерн из .process),
  `scripts/dev/golden_inspector.py` (разбор чужого файла).

## Структура

```text
SKILL.md            инструкции агента          main.md   канонический контекст
PLAN.md             план и статус фаз          references/  каталоги + форматы
scripts/            cli.py + sherpa_xml/ (ядро stdlib) + dev/ (инструменты, venv)
assets/             golden-шаблоны блоков      patterns/  эталонные конструкции
schemas/            sherpa-rules.sch           golden/    эталоны + manifest.yaml
tests/              pytest (51 тест)
```

## Разработка

```bash
.venv/Scripts/python.exe -m pytest tests/unit -q     # все тесты
.venv/Scripts/python.exe scripts/dev/gen_catalog.py  # каталоги из golden
.venv/Scripts/python.exe scripts/dev/extract_templates.py  # шаблоны из golden
```

## Ограничения (честно)

- Dictionary-начальные значения не генерируются (контейнер не подтверждён golden).
- Hashtable*-блоки операций ждут golden-образца (release их блокирует).
- Привязка к Designer 1.4.4; для других версий — новая golden-серия.
