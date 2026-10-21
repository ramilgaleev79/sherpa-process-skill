---
name: sherpa-process
description: Генерация, валидация, инспекция и точечная правка .process XML для Sherpa RPA Designer 1.4.4. Использовать всегда, когда пользователь просит создать процесс/сценарий/робота для Sherpa RPA, сгенерировать, проверить или изменить готовый .process (точечные правки без регенерации — что заденет, что доказуемо останется нетронутым), собрать процесс из описания, добавить блоки (Assign, Условие, Цикл Пока/Для, Try/Catch, пауза, коллекции, DataTable, переменные), модифицировать IR процесса, разобраться со структурой графа (GOTO-стрелки, вложенность контейнеров, хабы) или сериализацией .process. Триггерится на любые упоминания Sherpa RPA, Sherpa Designer, .process, .sherpa, Sherpa-процесс, сгенерировать робота/диаграмму, внести изменения в существующий сценарий.
---

# Sherpa Process Skill

Детерминированная генерация `.process` для Sherpa RPA Designer 1.4.4 из IR (JSON).

Каталожная база — **снятый с установленного Designer каталог**: 752 блока (все с
PowerShell-шаблоном `Code`), 746 из них в палитре, 11 типов переменных, 9 типов
свойств. Источник — `Designer/ExternalToolboxes/MainToolbox.xaml` (+ `Toolboxes-<lang>.json`
для подписей), см. `references/catalog-harvest.md`. Из 752 блоков 50 исходных остаются
golden-подтверждёнными (22 диаграммы), остальные подтверждены каталогом продукта и
replay-тестом корпуса (992/992 инстансов актуальных версий воспроизводятся точно).

## Железные принципы

1. **Ты никогда не пишешь .process XML руками.** Только IR (JSON); XML генерирует
   `scripts/cli.py build` из шаблонов каталога. Не декодируй Base64-контейнеры в уме —
   для этого инспектор.
2. **Catalog-over-invented.** Источник истины — `assets/block_index.json` (752 блока)
   и шаблоны в `assets/block_templates.json`. Нет блока в каталоге — значит его нет
   в этой сборке Designer: ставь узел `ToDo` с `unresolved_action` и предложи
   ближайший существующий блок (не «похожий по названию» — проверь каталог).
3. **Детерминизм.** Один IR → байт-в-байт один файл (uuid5-ID, фикс. порядок).
   `build` сам проверяет это и падает при нарушении.
4. **Профили**: draft (ToDo разрешён, предупреждения) / release (ToDo, необъявленные
   ссылки, неподключённые ветки Condition/While/For, не-Number индекс For блокируют
   выпуск; strict — надмножество release) / strict (+типовая строгость: kind=powershell
   на любом уровне AST, узлы InvokeExpression и raw-свойства вне грамматики запрещены).
5. **Каталог не правится руками.** Изменился Designer — перегенерируй каталог
   (`scripts/dev/harvest_catalog.py`) и прогони replay; ручные правки YAML/JSON бессмысленны.
6. **Правка живого сценария — с замером радиуса.** Готовый `.process` не
   перегенерируется. Сначала `scripts/structure_audit.py` (эталон «до») и расчёт
   побочных эффектов; если правка в одной точке расползается на весь сценарий —
   остановиться, показать «намеренно / побочно» и предложить варианты (локальный
   шов / смена контракта / отказ). Молча «минимальным диффом» не править.
   Подробно — `references/editing-existing-process.md`.

## Рабочий цикл

```bash
# 1. Скелет IR (Start → Assign → Stop) + перечень полей конструкций
python scripts/cli.py init-ir my.ir.json

# 2. Заполни IR (см. контракт ниже), затем валидация под профиль
python scripts/cli.py validate-ir my.ir.json --profile release

# 3. Сборка .process: валидация профиля → компиляция → пост-валидация XML
#    → детерминизм-чек; рядом с выходом пишется <out>.report.json
#    (+ <out>.todo.json — реестр нереализованных действий, если есть ToDo)
python scripts/cli.py build my.ir.json my.process --mode release
```

Обслуживание каталога (dev, только stdlib; нужна установка Sherpa RPA):

```bash
# снять каталог с установленного Designer (752 блока) + перегенерировать YAML-справочник
python scripts/dev/harvest_catalog.py

# проверить, что шаблоны воспроизводят 50 golden-блоков (ожидается 50/50)
python scripts/dev/harvest_catalog.py --check

# replay: отрендерить шаблоны значениями реальных .process и сравнить с оригиналом
python scripts/dev/replay_corpus.py --report replay.json
```

Python 3.10+, без зависимостей. Инспектор чужого `.process` (`scripts/golden_inspector.py`)
— тоже рантайм-инструмент, только stdlib.

## Контракт IR (кратко; полный — scripts/sherpa_xml/ir.py, ошибки подскажут)

- `process{name, mode}`, `variables[{key,name,sherpa_type,scope,initial}]`,
  `nodes[{key,type,…}]`, `edges[{from,from_port,to,to_port}]`.
- **Тип узла** — либо короткий алиас из `ir.NODE_TYPES` (Start, Assign, Condition,
  WhileLoop, ForLoop, TryCatch, Container, Stop, ToDo, Delay, List*/Table*/Collection*),
  либо **любой `InternalName` из каталога** (`Comment`, `ProcessContainer`, `ForeachRows`,
  `ForeachList`, `MouseClick`, `Log`, `SendMailMessage`, `Excel*`, `Orch*`, … — 752 варианта).
  Значения свойств такого блока задаются в `properties` (см. ниже).
- Типы переменных (11): Number, Logical, Text, SecureString, Hashtable, Dictionary,
  List, DataTable, Object, DateTime, Money.
- `initial`: number/boolean/string_literal — прямо; `datetime_literal` для DateTime
  (`dd.MM.yyyy HH:mm:ss`); коллекции — `list_literal`/`hashtable_literal`/
  `data_table_literal{columns,rows}`. Object/Money объявляются только пустыми.
- Строки в IR — сырые (без PS-кавычек/экранирования — их ставит компилятор).
- `properties` — raw PS-строки для любого блока; перекрытие типизированных полей
  (Condition, VariableN/ExpressionN, StartIndex/Step/Index, Expression1)
  — ошибка SHP-PROP-001; свойство вне шаблона блока — SHP-PROP-002;
  значение обязано быть строкой (SHP-PROP-003). В strict грамматика по @Type
  шаблона: Enum — вариант из EnumVariants (`Seconds`, `Ascending`), Bool —
  `True`/`False`, EnumMulti — `;`-список вариантов, Double — число,
  String/Selector/Image — как есть, Expression/Variable — простой операнд.
- **Порты** проверяются по каталогу: у блока нет такого порта — SHP-PORT-001
  (ERROR); превышен `MaxOutConnections`/`MaxInConnections` — SHP-PORT-002 (ERROR).
  `InputLeft` — равноправный вход (в корпусе 233+ связей), `Row`/`Item`/`Index` —
  выходы циклов, `InOut` — визуальная разводка (`PointConnector`).
- **Декоративные блоки** без портов (`Comment`) в поток не включаются: достижимость
  и путь к Stop для них не проверяются.
- Ветвление: Yes/No у Condition, Iteration/Next у WhileLoop, Index/Next у ForLoop,
  Next/Out/Error у контейнеров — обязательные выходы (SHP-GRF-020; draft WARN,
  release/strict блокируют). Граф — только рёбрами: поля branches/body/next
  игнорируются (SHP-IR-001). Тело контейнера — ветка `Next`, успех — `Out`.
- `external_variables[{name,expected_type}]` глушат SEM-REF-001 для входов робота.

## Справочники (читай по требованию)

- Каталог блоков (генерируется, 752 блока: порты, свойства, типы, версии):
  `references/block-catalog.yaml`; машинный индекс — `assets/block_index.json`.
- Как снимать/обновлять каталог и что подтверждено чем: `references/catalog-harvest.md`.
- Сериализация Text/контейнеров: `references/text-serialization.md`,
  `references/datatable-serialization.md`.
- Типы переменных и что наблюдалось в реальных процессах: `references/type-catalog.yaml`.
- Как строить граф из задачи: `references/graph-synthesis.md` (таблицы конструкций и портов).
- Правка готового сценария без регенерации (радиус, варианты, шаблон отчёта):
  `references/editing-existing-process.md`; инструменты — `scripts/structure_audit.py`
  (GOTO-аудит: классы стрелок, хабы, глубина, горячие переменные),
  `scripts/change_impact.py` (радиус планируемой правки по JSON-спецификации, до
  записи в файл) и `scripts/process_diff.py` (поблочное сравнение версий: что
  изменилось, а что нет).
- Корпус описаний и эталонов: `scripts/dev/corpus_mine.py` (статистика правил) и
  `scripts/dev/graph_compare.py` (оценка близости графов 0…1 и диффы).
- Что подтверждено какими эталонами и какие правила формата сняты с Designer: `golden/manifest.yaml`.
- Профили и блокирующие коды: `references/validation-profiles.yaml`; норматив
  правил — `scripts/sherpa_xml/validator.py` (коды SEM-*/SHP-*).

## Правила генерации (из ревью реальной задачи; эталон — golden/real/best-screenshots-mail-save в репозитории скила)

1. **Ничего сверх задачи.** Не добавляй паузы (Delay), подготовительные шаги,
   логирование — если они явно не написаны в задании.
2. **Не эмулируй отсутствующие блоки кодом.** PowerShell (InvokeExpression,
   powershell-выражения, regexp в Condition) — только когда пользователь ЯВНО
   просит код/выражение. Нет блока в каталоге → узел `ToDo`.
3. **ToDo = имя действия.** В `unresolved_action` пиши короткое действие из
   задачи («Сделать снимок экрана», «Отправить письмо», «Сохранить файл на
   диск») — компилятор ставит его в DisplayName блока. Не подробности реализации.
4. **Свойства — только явно требуемое.** Не заполняй свойства пустыми "" и
   дефолтными значениями: чего задача не задала — остаётся дефолтом шаблона.
5. **Выбор цикла.** Повторы заданы ЧИСЛОМ → `ForLoop` (1..N, index_variable,
   БЕЗ переменной-счётчика и инкремента). Обход таблицы —
   `ForeachRows` (порты `Next`/`Row`, свойства `Table`, `Row`, `RowIndex`), обход
   списка — `ForeachList`. Различать итерации («третий — почтой, остальные — на
   диск») → If по `$Index` в теле (условие вида `$Index -gt 2`), а не отдельные
   ветки снаружи. `WhileLoop` — только если условие не выражается диапазоном.
6. **DisplayName — смысловой**: условие → текст условия; ToDo → действие
   (п.3). Задаётся узлом `display_name` (для ToDo берётся из unresolved_action).
7. **Не выдумывай значения.** Адреса, порты, пути, которые задача не дала, —
   создавай переменные-заготовки ПУСТЫМИ (Text '', Number 0, SecureString ''),
   оператор заполнит. Не подставляй `company.local` и подобное.
8. **Обработка ошибок — свойство блока.** У каждого блока есть `ErrorLevel`,
   `ErrorDirection`, `ErrorMessage`, `ReportLevel` и выход `Error`; `TryCatch`
   нужен для обёртки группы, а не как единственный способ обработки.

## Сборка графа из задачи (по умолчанию; паттерны не нужны)

Алгоритм берётся **только из текста задачи**. Готовых процессов-заготовок в скилле
нет: каталог `patterns/` удалён (2026-10-20), потому что каждый паттерн нёс числа и
порядок блоков прошлой задачи (`EndIndex=5`, `Limit=3`, `NumberAttempts=3`) и при
копировании приносил чужой алгоритм. Остались только таблицы: какие конструкции
бывают, как они соединяются портами и как проверить результат.

Порядок работы (подробно — `references/graph-synthesis.md`):

1. **Таблица шагов**: выписать действия из задачи (действие / условие / повтор /
   ошибка / данные). Пока таблица не готова — блоки не выбирать.
2. **Конструкция по признаку**: «5 раз» → `ForLoop`; «для каждого» → `ForeachRows`/
   `ForeachList`; «пока» → `WhileLoop`; «если» → `Condition`; «повторить при ошибке»
   → `TryCatch`; ничего такого в задаче нет → конструкцию **не добавлять**.
3. **Блоки** — по глаголу из задачи, из `assets/block_index.json`; нет блока → `ToDo`.
4. **Связи** — по таблице портов: тело входит через `Index`/`Iteration`/`Row`/`Item`,
   последний блок тела возвращается в `Input` цикла, выход — `Next`; `No` → `InputLeft`.
5. **Ошибки** — только если задача про них говорит (`ErrorLevel=Handle`, `TryCatch`,
   `Error → Stop`).
6. **Проверка** — `validate-ir --profile release` → `build` → трассировка успеха,
   ошибки и границы цикла; каждый блок должен соответствовать строке задачи.

Таблицу шагов можно компилировать механически: `scripts/dev/plan_to_ir.py plan.json out.ir.json`
— строгая схема, автопроводка портов и коды `PLAN-*` (см. `references/graph-synthesis.md`).
Ручная проводка связей не нужна.

Если у пользователя есть чужой `.process` или найденный в сети пример — из него
берут **только устройство связей** (какой порт куда), но не числа, не порядок блоков
и не тела. Готовый фрагмент целиком не вставлять.

## Правка существующего сценария (не генерация)

Регенерация из IR для готового файла запрещена: перезапишет байты всех блоков и
потеряет layout/версии/свойства, которых в IR нет. Порядок обязателен (детали,
таблица несущих классов и шаблон отчёта — `references/editing-existing-process.md`):

1. **Эталон «до»**: `python scripts/structure_audit.py FILE.process` — классы
   стрелок, GOTO-список, хабы, глубина, мёртвые блоки, горячие переменные.
2. **Радиус правки**: описать правку спецификацией и прогнать
   `python scripts/change_impact.py FILE.process --spec spec.json` — до записи в файл.
   Четыре слоя: структурный (недостижимые блоки, смена контейнера, новые GOTO,
   обрывы потока), потоковый (хабы — входы из разных контекстов), данных (def-use:
   флаг `$needStop` пишется в одном месте и читается в шести) и контрактный (файлы,
   письма, параметры робота). Несущие конструкции — хаб / порт контейнера
   (`Next`/`Out`/`Error`) / число `pop` (`Конец`, `Прервать контейнер`) / флаг.
   Код возврата 1 = правка расползается, нужен разбор.
3. **Подсветка и варианты**: если вердикт «расползается» — не делать молча.
   Показать «намеренно / побочно» и предложить: локальный шов (разделить путь до
   несущего блока) / смена контракта (вынести участок в подпроцесс) / отказ с
   альтернативой.
4. **Правка**: только точечные вставки/замены в raw XML; новые блоки — клоны родных
   блоков того же файла (`Code`, `Version`, порты сохраняются), BOM и переводы
   строк — как в исходнике.
5. **Проверка**: `python scripts/process_diff.py OLD NEW` (что реально изменилось),
   `structure_audit.py` до/после (не выросли ли `goto-*`, хабы, мёртвые блоки),
   инварианты поведения. В отчёте — раздел «блоки не менялись, но изменили смысл».
   Байтовая идентичность ≠ неизменность поведения: это два разных отчёта.

## Расширение базы

- **Каталог**: `python scripts/dev/harvest_catalog.py` (после обновления Designer).
  Существующие golden-шаблоны сохраняются как есть (`--no-preserve` — заменить все).
- **Инспектор чужого .process** (рантайм, только stdlib): `scripts/golden_inspector.py FILE --deep`.
- **Приёмка после изменений**: `harvest_catalog.py --check` = 50/50, `replay_corpus.py`
  = 0 mismatch (legacy-code допустим), затем `cli.py build` демо-IR.

## Известные границы (не расширяй молча)

- Dictionary-переменные не генерируются: контейнер не подтверждён golden
  (SEM-VAR-010; временно — Hashtable).
- Непустые начальные значения Object/Money не подтверждены — объявляйте пустыми
  и присваивайте через Assign.
- Вложенные Hashtable/DataTable в ячейках таблиц — вне MVP.
- 6 блоков есть в движке, но не в палитре (`LaunchBrowserWithUrl`, `SetToUIText`,
  `SaveWordDocumentToPath`, `SaveExcelDocumentToPath`, `ReplaceTextInsideWordDocumentWithText`,
  `AddTextToParagraphWordDocument`) — генерируются, но в палитре Designer их не видно.
- Блоков, удалённых/переименованных в 1.4.4, в каталоге нет: `LaunchChrome`→`LaunchBrowser`,
  `LaunchIE`→`LaunchBrowser`, `RecognizeCaptcha`→`RecognizeCaptchaOCR`,
  `InternalBrowserGet`→`InternalBrowserAsyncGet`. Старые процессы с ними не воспроизводятся.
- `PathStyle`/`PathStyle_DragThumb` в шаблоны не встраиваются (как и в 50 golden):
  в XAML это ссылки `{StaticResource …}`. Если Designer начнёт требовать inline-стили —
  это отдельная фаза, менять только с проверкой загрузки.
- Для новых версий Designer — перегенерировать каталог; версии блоков плавают
  (в корпусе 1467 инстансов старее текущего каталога — это нормально).
