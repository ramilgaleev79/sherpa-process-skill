# План разработки скила sherpa-process

Version: 1.1-draft (2026-10-15) — ревизия с учётом существующего `sherpa-rules.sch`
Статус фаз на 2026-10-16: **0–6 завершены** (коммиты fa834b9…8c12ebd; 51 тест).
Открыто: фаза 7 — тестовые промпты и внешняя приёмка в Designer 1.4.4 (D8).
Входные документы: `main.md` (мастер-контекст v0.3-draft), `Правила Schematron для генератора процессов Sherpa RPA.md`, `sherpa-rules.sch` (единая структурная схема с реальными XPath Sherpa).
Статус окружения: Windows, Python 3.14.3 (stdlib only).

---

## 1. Цель и границы

Скил `sherpa-process` позволяет агенту ZCode по описанию пользователя собирать Process IR и детерминированно компилировать его в `.process` XML для Sherpa RPA, с двухслойной валидацией и политикой confirmed-over-invented (не подтверждено эталоном — не генерируется, вместо этого контролируемый `ToDo`-плейсхолдер в draft-режиме).

Ключевой принцип архитектуры: **модель (LLM) никогда не пишет `.process` XML руками**. Агент работает только с IR (JSON); всю генерацию, сериализацию бинарных контейнеров и валидацию выполняет детерминированный код из `scripts/`. Модель также не декодирует Base64-контейнеры вручную — для этого инструмент `inspect`.

Границы MVP — по разделу 27 `main.md` (граф Start/Stop/ToDo/Assign/Condition/While, скалярные и коллекционные переменные, подтверждённые операции блоков, профили draft/release/strict).

Вне границы MVP: вложенные сериализаторы (Hashtable/DataTable в ячейках таблицы), свободный PowerShell без типизации, заполненные `While`-условия как полностью подтверждённые, редкие числовые типы колонок.

---

## 2. Ключевые архитектурные решения (дефолты, можно поменять)

| # | Решение | Обоснование |
|---|---------|-------------|
| D1 | Ядро (`sherpa_xml`) — pure Python stdlib, без внешних зависимостей | Скил вызывается агентом в любой сессии; venv-зависимость в рантайме скила — точка отказа. Python 3.14 есть, lxml нет |
| D2 | IR — JSON, не YAML | PyYAML — внешняя зависимость (см. D1); YAML в `main.md` — только нотация документов |
| D3 | Разделение ответственности: Schematron — только структура и шаблоны блоков (слои structural + semantic-lite); Python — всё «умное»: Base64 и бинарные контейнеры, PowerShell-выражения, runtime-типы .NET, ссылки на переменные, детерминизм UUID/layout, SVRL-нормализация; release-профиль объединяет оба отчёта | Ревизия `sherpa-rules.sch` пользователем; согласуется с Schematron-документом («Schematron не должен быть единственным валидатором») |
| D4 | `schemas/sherpa-rules.sch` — единый рукописный канонический файл; развитие тремя логическими слоями внутри того же файла (`structural` / `semantic-lite` / `strict`); синхронизация с каталогами — consistency-тестом, а не генерацией | Решение пользователя: развивать существующий файл, а не генерировать с нуля; генерация из каталогов — отложенная опция после стабилизации |
| D9 | Процессор Schematron — XSLT 2.0+ (SaxonC / `saxonche`); `lxml.isoschematron` (XSLT 1.0) несовместим | `sherpa-rules.sch` объявляет `queryBinding="xslt2"` и использует XPath 2.0 (`distinct-values`); касается только dev/CI, не рантайма скила (D1) |
| D5 | Каталоги и справочники deriving из `main.md`, не из истории чатов | §31 `main.md` |
| D6 | Репозиторий скила — эта папка (`Sherpa_skill/`), корень скила = корень репо; установка — копия в `~/.agents/skills/sherpa-process/` (личный скил) или `.agents/skills/` проекта | Скил-креатор: скил = директория со SKILL.md;golden-файлы и тесты живут в репо, но не обязательны в установленной копии |
| D7 | Тесты — `unittest` (stdlib); pytest опционально в dev-venv | D1 |
| D8 | Приёмка артефакта — файл открывается и исполняется в целевой версии Sherpa Designer | Единственная внешняя проверка истины |

---

## 3. Целевая структура скил-пакета

```text
Sherpa_skill/                        # корень скила (репо)
├── SKILL.md                         # агентские операционные инструкции (frontmatter + workflow)
├── README.md                        # человеческое руководство
├── PLAN.md                          # этот план
├── main.md                          # канонический мастер-контекст (остаётся источником истины)
├── references/                      # читается агентом по требованию (progressive disclosure)
│   ├── block-catalog.yaml           # факты блоков: internal_name, version, порты, свойства
│   ├── type-catalog.yaml            # типы переменных, runtime-типы, формы сериализации
│   ├── validation-profiles.yaml     # draft / release / strict правила поведения
│   ├── expression-model.md          # категории выражений, правила компиляции, strictness
│   ├── text-serialization.md        # PS-литерал + бинарный контейнер + Base64 (включая PREFIX)
│   ├── datatable-serialization.md   # XSD + DiffGram + контейнер, подтверждённое подмножество
│   └── schematron-rules.md          # каталог SHP-* / SEM-*: контексты, роли, диагностика
├── scripts/
│   ├── cli.py                       # init-ir | build | validate | inspect | check-determinism
│   ├── sherpa_xml/                  # pure-stdlib пакет (§5 main.md)
│   │   ├── ir.py                    # dataclasses IR + load/dump JSON + проверка схемы IR
│   │   ├── identifiers.py           # uuid5(NAMESPACE_PROCESS, "process::node-key::Type")
│   │   ├── layout.py                # stable_incremental / lane_based, шаги 240×140
│   │   ├── compiler.py              # IR → XML (блоки, коннекторы, рёбра)
│   │   ├── serializer.py            # XML writer с фикс. порядком элементов/атрибутов
│   │   ├── expressions.py           # IR-выражения → PowerShell
│   │   ├── validator.py             # структурный + семантический, коды SHP-*/SEM-*
│   │   ├── profiles.py              # загрузка validation-profiles, режимы
│   │   ├── block_catalog.py         # доступ к каталогам, used by compiler & validator & gen-sch
│   │   └── types/
│   │       ├── primitives.py        # Number, Logical (declaration vs expression формы!)
│   │       ├── text.py              # контейнер: PREFIX + 7bit-len + UTF8(PS-literal) + 0x0B → Base64
│   │       ├── secure_string.py     # _tosecure/_fromsecure
│   │       ├── collections.py       # Hashtable/Dictionary/List (List initial serializer — после golden)
│   │       └── data_table.py        # schema + DiffGram + контейнер
│   └── dev/                         # dev-инструменты (могут требовать saxonche/pyyaml, только для разработки)
│       ├── golden_inspector.py      # дамп .process: Base64 → контейнер → побайтовый разбор
│       ├── golden_diff.py           # diff двух .process (см. «описание единственного изменения»)
│       ├── run_schematron.py        # SaxonC: .sch (фаза) → SVRL → нормализованный JSON-отчёт
│       └── consistency_check.py     # каталоги (block-catalog) ↔ .sch: InternalName, версии, обязательные свойства
├── golden/                          # эталонные .process + manifest.yaml
│   ├── manifest.yaml                # карта: файл → версия Designer → что подтверждает
│   └── *.process
├── schemas/
│   └── sherpa-rules.sch             # рукописный канонический файл (D4): structural / semantic-lite / strict
└── tests/
    ├── unit/                        # сериализаторы, идентификаторы, выражения, layout
    ├── golden/                      # byte-for-byte round-trip против эталонов
    ├── fixtures/schematron/         # SHP-*/valid|invalid.xml по правилу
    └── determinism/                 # повторная генерация byte-stable
```

Маппинг из `main.md` §28: `SKILL.md` ← агентные инструкции; `references/*` ← тематические разделы main.md; `schemas/sherpa-rules.sch` ← Schematron-документ; `main.md` остаётся каноническим контекстом.

---

## 4. Фазы

### Фаза 0 — Каркас и фиксация решений (~0.5 дня)

Задачи:
1. Утвердить решения D1–D8 (или поменять).
2. Создать структуру каталогов, черновик `SKILL.md` (name: `sherpa-process`, description с триггерами: «генерация/валидация .process Sherpa RPA, IR, детерминированный XML»).
3. Dev-venv: `python -m venv .venv`; `pip install saxonche pyyaml pytest`. Процессор — SaxonC (D9): `sherpa-rules.sch` объявляет `queryBinding="xslt2"` и использует XPath 2.0 (`distinct-values`), `lxml.isoschematron` несовместим. Рантайм скила от процессора не зависит (D1, D3); прогон .sch — только dev/CI.
4. Инициализировать git-репо (сейчас репо отсутствует) — история изменений критична для golden-диффов.

Выход: каркас на диске, `SKILL.md`-черновик загружается как скил.

### Фаза 1 — Golden-база (критический путь, ~1–2 дня + ожидание файлов)

Без эталонов генератор не имеет права генерировать (confirmed-over-invented). Это единственная фаза, зависящая от внешнего входа — Sherpa Designer.

Задачи:
0. Уже подтверждено самим `sherpa-rules.sch` и не требует повторного вывода из golden: реальная XML-модель — корень `Root`, контейнер `Properties`, `DiagramProperty/@(Name, Type, IsGlobal, DefaultValue, Value)`, блоки `ToolboxItemSettings/@(InternalName, Version, DisplayName)`, свойства `Property/@(Name, Type, ValueType, Value, IsPSVerified)`, коннекторы `Connector/@Name`; InternalName-ы и версии всех подтверждённых блоков (Assign 166, IfElse 23, While 32, операции Hashtable*/List*/DataTable*); матрица `Property/@ValueType` (IfElse/Condition → `Logical`, While/Condition → `AnyDataType`, Assign/ExpressionN → `AnyDataType`, Items → `Hashtable`/`List`, Table → `DataTable`). Golden нужны для остального: бинарные контейнеры (PREFIX), сериализации, поведенческие детали.
1. Минимальная диагностическая серия (из Schematron-документа «Данные для следующего шага»): `Start→End`; +один промежуточный блок; два одинаковых блока; ветвление; одна переменная; для каждой пары — описание единственного изменения. Зафиксировать точную версию Designer.
2. Приоритетная серия (§30 main.md): P1 `While` с заполненным условием (`$Counter -lt 3`); P2 `List` declaration (пустой + 2–3 значения) — сравнение Base64; P3 вложенные ячейки DataTable (Hashtable, DataTable); P4 составное условие `($Number -gt 5) -and ($Text -eq "Test")`; P5 редкие типы колонок.
3. `dev/golden_inspector.py`: well-formed parse → дамп структуры, декодирование `DiagramProperty` Base64 → побайтовый разбор контейнера (PREFIX, 7bit-length, payload, терминатор 0x0B), разбор DataTable-контейнера (XmlSchema/XmlDiffGram).
4. `dev/golden_diff.py`: точечный diff пар файлов.
5. Зафиксировать в `references/text-serialization.md` конкретное значение PREFIX (сейчас в main.md оно не записано) и все неизвестные байты контейнеров.
6. Обновить `main.md`: перенести подтверждения, закрыть открытые вопросы §29 по мере подтверждения; `golden/manifest.yaml`.
7. Самопроверка правил: прогнать `sherpa-rules.sch` (фаза `strict`) через SaxonC на каждом golden-файле — эталоны, собранные Designer'ом, должны проходить без `failed-assert`; каждое срабатывание — либо неточность правила, либо новое подтверждение для main.md.

Выход: manifest с картой подтверждений; PREFIX и бинарные форматы зафиксированы побайтово; открытые вопросы §29 сокращены.

Критерий: inspector декодирует все golden-файлы без «неизвестных байт» в подтверждённых областях.

### Фаза 2 — IR и каталоги (~1–2 дня)

Задачи:
1. `ir.py`: dataclasses (Process, Variable, Node, Edge, выражения из §19), JSON load/dump, проверка IR-схемы (unknown-поля — ошибка, чтобы IR оставался закрытым контрактом).
2. `cli.py init-ir`: шаблон IR с примерами узлов всех подтверждённых типов.
3. Каталоги `block-catalog.yaml`, `type-catalog.yaml`, `validation-profiles.yaml` — машиночитаемая выжимка из main.md (D5), включая: logical→physical имена (`CollectionSetItem`→`HashtableAddItem`), формы ссылок (block `name` vs expression `$name`), порядок коннекторов (`Condition: [Yes,No,Error]`, `While: [Iteration,Next]`).
4. Поддержка `external_variables` и unknown-reference policy (warn по умолчанию, §24).
5. Наполнить каталоги также фактами из `sherpa-rules.sch`: `Property/@IsPSVerified`, кодирование ссылок (`VariableN` — с `$`, `Items`/`Table` — без), фиксированные `ValueType`-матрицы по блокам — компилятор и схема обязаны соглашаться.

Выход: IR round-trip (load→dump) байт-стабилен; каталоги валидируются при загрузке.

Критерий: `init-ir` → редактирование → `build` даёт воспроизводимый результат.

### Фаза 2.5 — Extension Pipeline (добавлено по требованию пользователя)

Конвейер расширения базы знаний скила — обязателен, т.к. каталог блоков и confirmed-набор
будут расти от реальных .process, а не только от целевых golden-серий.

**Ingest нового блока** (`scripts/dev/ingest_block.py`):
1. Пользователь кладёт .process с новым блоком → `ingest_block.py FILE [имя_в_golden]`.
2. Скрипт: копирует в `golden/real/`, прогоняет `gen_catalog.py`, сравнивает каталог с `ir.NODE_TYPES`.
3. Выводит: новые блоки + готовые строки `LogicalName: InternalName` для вставки в NODE_TYPES,
   конфликты версий (новая версия известного блока — попадает в confirmed_versions автоматически).
4. Разработчик добавляет логическое имя в `NODE_TYPES` (выбор логического имени —/design-решение,
   не автоматизируется), тест `test_catalog_sync` держит соответствие.
Никакой блок не попадает в генератор в обход golden (confirmed-over-invented сохраняется).

**Библиотека эталонных паттернов** (`patterns/`):
- Паттерн = проверенное golden-ом решение из нескольких блоков: retry-обёртка TryCatch,
  цикл-счётчик While, ветвление с прерыванием и т.п.
- Формат: `patterns/<name>/pattern.json` — `{meta, parameters, fragment}`; fragment — валидный
  IR-фрагмент (узлы/рёбра/переменные), вставляемый агентом в IR нового процесса
  (вставка = копирование узлов с уникальными ключами + стыковка рёбер по entry/exit).
- Создание: `scripts/dev/extract_pattern.py` — из golden .process выделяет подграф → черновик
  pattern.json с raw-свойствами (физические имена, PS-значения) → нормализация в логический IR
  вручную/агентом → валидация тестом.
- SKILL.md описывает агенту: когда искать паттерн, как вставлять, что паттерн — не блок каталога.

Выход фазы 2.5: два скрипта конвейера, 2+ эталонных паттерна, тесты, обновлённый SKILL.md.

### Фаза 3 — Сериализаторы типов (~2–4 дня)

Порядок — от простого к бинарному, каждый сверяется с golden побайтово:

1. `identifiers.py` — uuid5 от (process name, node key, block type, stable path); golden ID никогда не копируются (§7.2).
2. `layout.py` — детерминированная раскладка.
3. `types/primitives.py` — Number; Logical в двух формах (`True/False` declaration, `$True/$False` expression).
4. `types/text.py` — PS-литерал (экранирование `` ` ```, кавычек, `` `n ``) → UTF-8 → контейнер `PREFIX + 7bit-len + bytes + 0x0B` → Base64. Тесты: empty, ascii, unicode (кириллица), кавычки, переносы.
5. `types/secure_string.py` — пустая декларация; `_tosecure`/`_fromsecure` в выражениях.
6. `types/data_table.py` — XSD-схема → DiffGram (`tmpDataSet`/`Table1`, row `Table1{N}` с 1, order с 0, `hasChanges=inserted`, null = отсутствие элемента) → бинарный контейнер → Base64. Подтверждённые типы ячеек: String, Int32, Double, Decimal, DateTime (ISO 8601 + offset), Boolean (`true/false` — отличается от Logical!), Guid, DBNull.
7. `types/collections.py` — блоки операций подтверждены; начальный сериализатор List — только после golden P2 (до тех пор: пустой шаблон или runtime-заполнение блоками).

Выход: функции serialize/deserialize с эталонными парами из golden.

Критерий: `deserialize(golden) → serialize → byte-equal` для каждого подтверждённого значения.

### Фаза 4 — Компилятор и граф (~2–3 дня)

Задачи:
1. `serializer.py` — XML writer с явным контролем порядка элементов и атрибутов, единая точка XML-экранирования (в IR — неэкранированные строки, §19.2).
2. `compiler.py`:
   - Start/Stop (обязательные узлы графа);
   - `ToDo` — pass-through + `Next`, попадает в unresolved registry;
   - `Assign` — до 10 пар `VariableN/ExpressionN`, неиспользуемые пары остаются в шаблоне (§15.2), target с `$`;
   - `Condition` (IfElse v23) — коннекторы Yes/No/Error, никакого `Next`; пустое условие — ошибка в release;
   - `While` (v32) — Iteration/Next, тело возвращается в `While/Input`; заполненное условие — в release только после golden P1;
   - блоки коллекций/List/DataTable через логические имена с диспетчеризацией по типу переменной;
   - фиксированный порядок коннекторов и рёбер (§25).
3. `expressions.py` — переменные → `$name`, агрессивные скобки, только подтверждённые категории; strict — без implicit truthiness.

Выход: полный конвейер IR → `.process`.

Критерий: минимальные golden-процессы регенерируются байт-в-байт (или структурно эквивалентно с зафиксированным списком допустимых расхождений — layout, ID).

### Фаза 5 — Валидация (~2–3 дня)

Задачи:
1. `validator.py` — структурный слой: коды `SHP-DOC/ID/GRF/BLK/PRP/VAR/TODO/SEC` из Schematron-документа; достижимость `GRF-010/011` — Python-норматив (в XPath 1.0 непереносимо).
2. `validator.py` — семантический слой: `SEM-EXPR/TYPE/SER/LOOP/REF` — decode Base64 и проверка контейнеров, ссылки на переменные (включая external), type compatibility, эвристики `While` (§17.7: тело без изменения состояния, статически всегда true).
3. `profiles.py`: draft (ToDo разрешён) / release (ToDo, пустое While-условие, неподтверждённые ветки — блокируют) / strict (+ типизация выражений).
4. Развитие `sherpa-rules.sch` как рукописного канона (D4):
   - развести три канонические фазы-слоя — `structural` (структура, обязательные атрибуты, коннекторы, шаблоны блоков), `semantic-lite` (проверки без декодирования: парность VariableN/ExpressionN, enum-значения, кодирование ссылок `$`) и `strict` (= structural + semantic-lite, для release-профиля); сейчас все три фазы активируют одинаковый набор patterns и являются заглушкой;
   - добавить на каждый `sch:assert` атрибуты `id` (код `SHP-*`) и `role` (`FATAL`/`ERROR`/`WARN`) плюс `sch:diagnostics` — сейчас коды и роли зашиты в текст ассертов, SVRL без `@id`/`@role` не разбирается машиной без regex по тексту (это прямое требование Schematron-документа проекта);
   - сознательно НЕ переносить в .sch: Base64, бинарную сериализацию Text/DataTable, PowerShell AST, runtime-типы .NET, разрешение ссылок на переменные, детерминизм UUID/layout — это зона Python (D3).
5. `dev/run_schematron.py` (SaxonC, D9): SVRL → нормализованный JSON (code, severity, location, message); release-профиль в `cli.py validate` объединяет SVRL + Python-отчёт в единый результат (D3).
6. `dev/consistency_check.py`: block-catalog ↔ .sch — InternalName, версии блоков, обязательные свойства и порты не расходятся между компилятором и схемой; запуск в CI при изменении любого из двух.
7. Фикстуры `tests/fixtures/schematron/SHP-*/{valid,invalid}.xml` — минимум пара на правило + mutation-набор (удалить атрибут, дублировать ID, подставить неизвестный порт, добавить timestamp).

Выход: `cli.py validate --profile=strict|release|draft` с единым машинным отчётом (code, severity, location, message), объединяющим Python-слой и SVRL; `check-determinism` — двойная генерация + byte-compare.

Критерий: каждая мутация ловится ровно ожидаемым кодом правила.

### Фаза 6 — CLI, SKILL.md и агентский слой (~1–2 дня)

Задачи:
1. `cli.py build` — конвейер целиком: IR → compile → structural → semantic → determinism → `.process`; флаг `--mode`; при блокировке — отчёт с кодами, а не стек-трейс.
2. `SKILL.md` — операционные инструкции агенту:
   - триггеры и режимы;
   - workflow: уточнить требования → `init-ir` → заполнить IR (никогда не писать XML руками) → `build --mode=draft` → разобрать отчёт → устранить или зафиксировать ToDo в unresolved registry → `validate --profile=release`;
   - confirmed-over-invented: если блока нет в каталоге — ToDo + запись в реестр, не «похожий» блок;
   - когда читать какой `references/*` файл;
   - работа с неизвестными конструкциями: предложить пользователю создать golden-образец (петля расширения).
3. Установка скила: копия в `~/.agents/skills/sherpa-process/` (или `.agents/skills/` проекта — решить по месту использования).
4. `README.md` — руководство для человека.

Выход: скил обнаруживается ZCode, полный сценарий проходит по CLI.

Критерий: свежая сессия с установленным скилом проходит end-to-end сценарий без ручных правок XML.

### Фаза 7 — Оценка и итерация (непрерывно, ~2–3 дня начального цикла)

Цикл skill-creator: черновик → тестовые промпты → разбор результатов → правки.

Тестовые промпты (реалистичные, по нарастанию):
1. «Сгенерируй процесс для Sherpa: переменная Counter = 0, цикл пока Counter < 3 с инкрементом, потом Stop» — While + Assign + Number.
2. «Процесс: Text-переменная с "Он сказал "Тест"", условие по ней, ветка Yes — Assign, ветка No — Stop» — Text-сериализация, Condition, экранирование.
3. «DataTable с колонками Name(String), Created(DateTime), Id(Guid), три строки, одна с пустым Created» — DiffGram, DBNull.
4. (draft-режим) «Добавь отправку письма через Outlook» — ожидаемо ToDo + запись в unresolved registry, агент не выдумывает блок.

Внешняя приёмка: каждый результат открывается и запускается в целевой версии Sherpa Designer (D8).

---

## 5. Риски

| Риск | Влияние | Митигация |
|------|---------|-----------|
| Нет доступа к Sherpa Designer / golden-файлы не собраны | Блокируется всё: даже PREFIX текстового контейнера не зафиксирован | Фаза 1 — первый приоритет; генератор до golden не пишет ничего, кроме подтверждённого |
| Бинарный контейнер DataTable сложнее описанного (nested объекты, метаданные) | Срыв фазы 3 | Побайтовый разбор inspector'ом до начала `data_table.py`; MVP-граница §14.9 |
| Версии блоков различаются между версиями Designer (Assign 166 / IfElse 23 / While 32) | Несовместимость сгенерированного | Профиль версии в manifest golden + `validation-profiles`; при смене версии — новая серия эталонов |
| saxonche (SaxonC) недоступен/нестабилен на Py3.14/Win | Прогоны .sch в dev/CI (фазы 1 и 5) | Рантайм скила не зависит (D1, D3); fallback: .sch остаётся декларативной спецификацией с отложенными прогонами; альтернатива — Saxon под Java вне Python-стека |
| Детерминизм нарушается скрытым полем (timestamp, автогенерация Designer) | Невоспроизводимые файлы | `check-determinism` в каждом CI-прогоне; `SHP-DET-*` правила |
| Изменение формата `.process` в новых версиях Designer | Регрессии | Golden-манифест с версией; round-trip тесты как детектор |
| Переусложнение SKILL.md (over-prescribing) | Агент делает лишнюю работу | Цикл фазы 7: обрезать, а не добавлять правила (принцип skill-creator) |

---

## 6. Открытые вопросы (решить при старте соответствующей фазы)

1. Есть ли сейчас доступ к Sherpa Designer и целевая версия? (определяет старт фазы 1)
2. Скил личный (`~/.agents/skills/`) или проектный (`.agents/skills/` конкретного репо)?
3. Json IR достаточно, или пользовательский ввод удобнее описывать в YAML (тогда PyYAML в dev-venv + конвертация)?
4. Требуется ли round-trip `.process → IR` (парсер эталонов как полноценная фича), или только byte-stable генерация? (парсер половинно нужен для тестов уже в фазе 3)
5. Target-платформа роботов: только Windows, или учитывать `linux_available` флаги блоков?

---

## 7. Порядок действий (сводка)

```text
Фаза 0  каркас + решения + git + dev-venv
Фаза 1  golden-серия + inspector + фиксация PREFIX и форматов   ← критический путь
Фаза 2  IR + каталоги + init-ir
Фаза 3  сериализаторы (uuid5 → layout → primitives → text → secure → datatable → collections)
Фаза 4  XML writer + компилятор блоков + граф + выражения
Фаза 5  валидация (Python норматив + генерируемый Schematron) + профили + фикстуры
Фаза 6  CLI + SKILL.md + установка скила + README
Фаза 7  тестовые промпты → приёмка в Designer → итерации
```

Оценка суммарно: ~11–18 рабочих дней чистой разработки плюс время сбора golden-образцов.
