# sherpa-process

Детерминированная генерация `.process` XML для **Sherpa RPA Designer 1.4.4**
из промежуточного представления (IR, JSON), с двухслойной валидацией и профилями
draft/release/strict. Политика confirmed-over-invented: 50 блоков подтверждены
golden-эталонами (22 диаграммы + файл проекта Designer), остальные — каталогом
продукта и replay-тестом корпуса; типы и форматы сериализации подтверждены golden.

Скил в формате Agent Skills (`SKILL.md`) — работает с любым AI-агентом,
поддерживающим этот формат. Устанавливается в `~/.agents/skills/` — стандартную
кросс-агентную директорию персональных скиллов.

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

## Установка скила (runtime-копия)

```bash
git clone https://github.com/ramilgaleev79/sherpa-process-skill
cd sherpa-process-skill
python scripts/install_skill.py             # -> ~/.agents/skills/sherpa-process
python scripts/install_skill.py --force     # перезаписать цель без маркера sherpa-process
```

Ставится только рантайм по white-list (CLI, инспектор, `sherpa_xml`, шаблоны
блоков, справочники, `golden/manifest.yaml`) — ~8.5 МБ. Если цель существует и
в её `SKILL.md` нет маркера `name: sherpa-process`, установщик откажется
удалять каталог без `--force`.
Golden-файлы, `scripts/dev/`, `schemas/` и доки репозитория остаются в репозитории.

## Команды CLI

| Команда | Назначение |
|---|---|
| `init-ir FILE` | шаблонный IR со всеми основными узлами |
| `validate-ir FILE [--profile …] [--report …]` | проверка IR: контракт + SEM-*/SHP-*/SHP-GEN-* правила; машинный отчёт JSON |
| `build IR OUT [--mode draft\|release\|strict]` | сборка: валидация профиля → компиляция → **пост-валидация XML** → детерминизм-чек; артефакты `OUT`, `OUT.report.json` (в т.ч. при провале), `OUT.todo.json` (при ToDo) |

## Инструменты (рантайм, stdlib)

| Инструмент | Назначение |
|---|---|
| `scripts/lint_process.py FILE` | линт чужого `.process`: P0/P1/P2 — секреты, дубли ID, висячие связи, обрывы, выключенные блоки, мёртвый код, блоки вне каталога, версии. Он же — `cli.py lint-process FILE` |
| `scripts/structure_audit.py FILE` | GOTO-аудит: классы стрелок, GOTO назад/наружу/вглубь, хабы, глубина, горячие переменные |
| `scripts/golden_inspector.py FILE --deep` | дамп блока: свойства, порты, Base64-контейнеры |
| `scripts/change_impact.py FILE --spec spec.json` | радиус правки по спецификации (до записи): структура, хабы, данные, вердикт |
| `scripts/apply_spec.py FILE --spec spec.json --backup` | запись правки по спецификации: клоны родных блоков, BOM/CRLF, самопроверка до записи |
| `scripts/process_diff.py OLD NEW [--expect spec.json]` | что реально изменилось; `--expect` — дифф обязан совпасть со спецификацией |
| `scripts/block_suggest.py "отправить письмо"` | кандидаты блоков по глаголу задачи (корпусный индекс `assets/verb_block_index.json`) |

Один контракт на три инструмента правки — `scripts/edit_spec.py`: «что считали»
(`change_impact`), «что записали» (`apply_spec`) и «что доказали» (`process_diff
--expect`) не могут разойтись.

## Ревью и правка чужого сценария

- Ревью: `lint_process` → `structure_audit` → `golden_inspector` для спорных блоков →
  смысл глазами. Чек-лист и шаблон отчёта — `references/reviewing-a-process.md`.
- Правка: `structure_audit` (до) → `change_impact` (радиус) → `apply_spec` (запись) →
  `process_diff --expect` (доказательство). Детали — `references/editing-existing-process.md`.


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
- **Валидатор** (`validator.py`) — Python-слой, норматив: граф (обязательные
  ветки Condition/While/For/Foreach и контейнеров — SHP-GRF-020; границы For —
  SHP-GRF-021), типы, выражения (powershell запрещён
  в strict на любом уровне AST), контейнеры, raw-перекрытия типизированных полей
  (SHP-PROP-001 — всегда ERROR), свойства вне golden-шаблона (SHP-PROP-002).
  Блок без golden-шаблона — ERROR во всех профилях (SHP-BLK-010, build невозможен).
  Пост-валидация сгенерированного XML встроена в `build` (последний рубеж):
  уникальность ID, парсинг Content, InternalName (членство в каталоге)/Version,
  коннекторы против портов и направлений блоков, арность max_in/max_out,
  DefaultValue И Value, пустой SecureString, UTF-8.
  Schematron (`schemas/sherpa-rules.sch`) — dev-аудит без обязательной синхронизации
  с Python; опциональный перекрёстный прогон при ingest нового golden:
  `scripts/dev/run_schematron.py FILE --phase strict` (SaxonC).
- **Профили**: strict структурно надмножество release (`profiles.py` собирает его
  объединением; тест фиксирует). Dictionary-переменные не генерируются (SEM-VAR-010).
  Raw `properties` в strict — грамматика по @Type шаблона: Enum — вариант из
  EnumVariants, Bool — True/False, Expression/Variable — простой операнд (SEM-PROP-002);
  InvokeExpression в strict запрещён.
- **Синтез графа** (`references/graph-synthesis.md`) — как строить процесс из задачи;
  готовых процессов-заготовок нет: алгоритм выводится только из текста задачи.
- **Правила генерации с машинной проверкой** (`SHP-GEN-*`, всегда WARN): свойство,
  равное дефолту шаблона; длинный `unresolved_action` (ToDo = действие); выдуманная
  инфраструктура в значении; секрет в значении. Правило без проверки перестаёт
  различать случаи, поэтому три из восьми правил проверяются до сборки.
- **Корпусный индекс** `assets/verb_block_index.json` (глагол → блок по реальным
  процессам, `scripts/dev/build_verb_index.py`) и `scripts/block_suggest.py` —
  кандидаты блоков на шаге 3 синтеза графа.
- **Eval-пары** (`tests/fixtures/evals/`): задача → план → IR → build →
  `graph_compare` против эталона (диаграмма из Designer, фиксированная release-сборка,
  верхний уровень многослойного сервиса). Правка правил или компилятора проверяется числом:
  `tests/unit/test_evals.py`.
- **Инспектор** (`scripts/golden_inspector.py`, только stdlib) — рантайм-инструмент:
  разбор чужого `.process` (структура, Base64-контейнеры, побайтовый разбор `--deep`).
- **Обслуживание библиотеки** (репозиторий скила, venv): `scripts/dev/harvest_catalog.py`
  (снять 752 блока с установленного Designer), `scripts/dev/replay_corpus.py` (прогон по корпусу).
 

## Структура

```text
SKILL.md            инструкции агента          references/  каталоги + форматы
scripts/            cli.py + sherpa_xml/ (рантайм, stdlib) + файловые инструменты:
                    lint_process.py, structure_audit.py, golden_inspector.py,
                    change_impact.py, apply_spec.py, edit_spec.py, process_diff.py,
                    block_suggest.py; dev/ — инструменты репо (venv); install_skill.py
assets/             golden-шаблоны блоков + block_index + verb_block_index
schemas/            sherpa-rules.sch (dev-аудит)  golden/ эталоны + manifest.yaml
tests/              pytest; fixtures/evals/ — пары «задача → план → эталон»
```

## Разработка

```bash
.venv/Scripts/python.exe -m pytest tests/unit -q          # все тесты (включая eval-пары)
.venv/Scripts/python.exe scripts/dev/harvest_catalog.py      # библиотека из установленного Designer
.venv/Scripts/python.exe scripts/dev/harvest_catalog.py --check   # 752/752, из них 50 golden
.venv/Scripts/python.exe scripts/dev/replay_corpus.py         # прогон по корпусу .process
.venv/Scripts/python.exe scripts/dev/build_verb_index.py      # пересборка корпусного индекса глаголов
```

## Ограничения (честно)

- Dictionary-переменные не генерируются (контейнер не подтверждён golden; SEM-VAR-010).
- Hashtable*-блоки операций собираются из каталожных шаблонов; их значения проверяются
  по шаблону, но отдельного golden-образца операций нет.
- Привязка к Designer 1.4.4; для других версий — новая golden-серия.

## Лицензия

Код и документация — [MIT](LICENSE), © 2026 GaleevRR.
Исключение — данные каталога блоков (`assets/block_templates.json`,
`assets/block_index.json`, `references/block-catalog.yaml`): сняты с
установленного Sherpa RPA Designer 1.4.4 и принадлежат его правообладателю;
в репозитории — для совместимости (см. Third-party data notice в LICENSE).
