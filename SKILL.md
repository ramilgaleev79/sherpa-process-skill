---
name: sherpa-process
description: Генерация, валидация, инспекция, ревью и точечная правка .process XML для Sherpa RPA Designer 1.4.4. Использовать всегда, когда пользователь просит создать процесс/сценарий/робота для Sherpa RPA, сгенерировать, проверить или изменить готовый .process (точечные правки без регенерации — что заденет, что доказуемо останется нетронутым), провести ревью чужого сценария (дефекты, секреты, GOTO-стрелки, мёртвый код, отчёт с приоритетами), собрать процесс из описания, добавить блоки (Assign, Условие, Цикл Пока/Для, Try/Catch, пауза, коллекции, DataTable, переменные), модифицировать IR процесса, разобраться со структурой графа (GOTO-стрелки, вложенность контейнеров, хабы) или сериализацией .process. Триггерится на любые упоминания Sherpa RPA, Sherpa Designer, .process, .sherpa, Sherpa-процесс, сгенерировать робота/диаграмму, внести изменения в существующий сценарий (в т.ч. переименовать переменную по всему процессу — операцией rename_variables, семантика Designer), отревьюить/проверить чужой робот.
when_to_use: Применяй при любом упоминании Sherpa RPA / Sherpa Designer / .process / .sherpa — создать робота из описания, изменить готовый .process (включая переименование переменной), отревьюить чужой сценарий, разобрать граф или сериализацию. Не для установки и администрирования самого Sherpa RPA.
---

# Sherpa Process Skill

Детерминированная генерация `.process` для Sherpa RPA Designer 1.4.4 из IR (JSON).

Каталожная база — **снятый с установленного Designer каталог**: 752 блока (все с
PowerShell-шаблоном `Code`), 746 из них в палитре, 11 типов переменных, 9 типов
`@Type` свойств (грамматические классы strict; `ValueType` — отдельная таксономия).
Источник — `Designer/ExternalToolboxes/MainToolbox.xaml` (+ `Toolboxes-<lang>.json`
для подписей), см. `references/catalog-harvest.md`. Форматные факты (порядок
секций Root, поля DesignerItem/Connection, Content=XamlWriter.Save, семантика
рефакторинга) дополнительно подтверждены статическим разбором самого
DiagramDesigner.exe 1.4.4.40199 — IL-провенанс в `golden/manifest.yaml`. Из 752 блоков 50 исходных остаются
golden-подтверждёнными (22 диаграммы), остальные подтверждены каталогом продукта и
replay-тестом корпуса: **988 exact + 42 legacy-code, 0 mismatch** (из 1030 инстансов
актуальных версий; ещё 1469 — старее каталога, 30 — без шаблона; всего 2529).

## Железные принципы

1. **При генерации ты не пишешь .process XML руками.** Источник — IR (JSON); XML
   генерирует `scripts/cli.py build` из шаблонов каталога. Исключение — точечная
   правка готового файла (раздел «Правка существующего сценария»): там меняется
   raw XML по замерам радиуса, но не пересобирается весь процесс из IR. Не декодируй
   Base64-контейнеры в уме — для этого инспектор.
2. **Catalog-over-invented.** Источник истины — `assets/block_index.json` (752 блока)
   и шаблоны в `assets/block_templates.json`. Нет блока в каталоге — значит его нет
   в этой сборке Designer: ставь узел `ToDo` с `unresolved_action` и предложи
   ближайший существующий блок (не «похожий по названию» — проверь каталог).
3. **Детерминизм.** Один IR → байт-в-байт один файл (uuid5-ID, фикс. порядок).
   `build` сам проверяет это и падает при нарушении.
4. **Профили**: draft (ToDo разрешён, предупреждения) / release (ToDo, необъявленные
   ссылки, неподключённые ветки Condition/While/For/Foreach и контейнеров,
   не-Number индекс For и границы For блокируют выпуск) / strict (надмножество
   release + типовая строгость: kind=powershell на любом уровне AST, узлы
   InvokeExpression и raw-свойства вне грамматики запрещены).
5. **Каталог не правится руками.** Изменился Designer — перегенерируй каталог
   (`scripts/dev/harvest_catalog.py`) и прогони replay; ручные правки YAML/JSON бессмысленны.
6. **Правка живого сценария — с замером радиуса.** Готовый `.process` не
   перегенерируется. Переменные переименовываются только операцией
   `rename_variables` (семантика Designer), никогда — текстовой заменой. Сначала `scripts/structure_audit.py` (эталон «до») и расчёт
   побочных эффектов; если правка в одной точке расползается на весь сценарий —
   остановиться, показать «намеренно / побочно» и предложить варианты (локальный
   шов / смена контракта / отказ). Молча «минимальным диффом» не править.
   Подробно — `references/editing-existing-process.md`.

## Рабочий цикл

```bash
# 1. Скелет IR (Start → Assign → Stop) + перечень полей конструкций
python scripts/cli.py init-ir my.ir.json

# 2. Заполни IR (см. контракт ниже), затем валидация под профиль.
#    Если по правилу 2 остались ToDo — для черновика используйте --profile draft.
python scripts/cli.py validate-ir my.ir.json --profile release

# 3. Сборка .process: валидация профиля → компиляция → пост-валидация XML
#    → детерминизм-чек; рядом с выходом пишется <out>.report.json
#    (+ <out>.todo.json — реестр нереализованных действий, если есть ToDo)
python scripts/cli.py build my.ir.json my.process --mode release

# Если по правилу 2 остались ToDo — release их блокирует (SHP-TODO-001).
# Для черновика используйте draft; после замены ToDo повторите release.
python scripts/cli.py validate-ir my.ir.json --profile draft
python scripts/cli.py build my.ir.json my.process --mode draft
```

Обслуживание каталога (dev, только stdlib; нужна установка Sherpa RPA):

```bash
# снять каталог с установленного Designer (752 блока) + перегенерировать YAML-справочник
python scripts/dev/harvest_catalog.py

# проверить, что все шаблоны совпадают с текущим MainToolbox.xaml
# (ожидается 752/752, из них 50 golden; индекс не должен быть stale)
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
  List, DataTable, Object, DateTime, Money. **Dictionary принимается контрактом, но
  объявление запрещено валидатором (SEM-VAR-010): используйте Hashtable** — см.
  «Известные границы».
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
  `InputLeft` — равноправный вход (326 связей в текущем корпусе; воспроизводится
  `python scripts/dev/corpus_stats.py`), `Row`/`Item`/`Index` —
  выходы циклов, `InOut` — визуальная разводка (`PointConnector`).
- **Декоративные блоки** без портов (`Comment`) в поток не включаются: достижимость
  и путь к Stop для них не проверяются.
- Ветвление: Yes/No у Condition, Iteration/Next у While, Index/Next у For,
  Row/Next у ForeachRows, Item/Next у ForeachList, Next/Out/Error у TryCatch,
  Next/Out у CollapseContainer, Next/Error у ProcessContainer — обязательные
  выходы по фактическим портам шаблона (SHP-GRF-020; draft WARN, release/strict
  блокируют). Граф — только рёбрами: поля branches/body/next игнорируются
  (SHP-IR-001). Тело контейнера — ветка `Next`, успех — `Out`.
- `For` требует `end_index`; `step=0` запрещён (SHP-GRF-021; draft WARN,
  release/strict блокируют).
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
  записи в файл), `scripts/apply_spec.py` (запись той же спецификации с бэкапом и
  самопроверкой) и `scripts/process_diff.py` (поблочное сравнение версий:
  что изменилось, а что нет; `--expect spec.json` — дифф обязан совпасть со спекой).
- Ревью чужого сценария (что искать, порядок, шаблон отчёта):
  `references/reviewing-a-process.md`; инструмент — `scripts/lint_process.py`
  (дефекты P0/P1/P2: секреты, висячие связи, обрывы, выключенные блоки, каталог, версии).
- Корпус описаний и эталонов: `scripts/dev/corpus_mine.py` (статистика правил),
  `scripts/block_suggest.py` (кандидаты блоков по глаголу из задачи — корпусный
  индекс `assets/verb_block_index.json`) и `scripts/dev/graph_compare.py`
  (оценка близости графов 0…1 и диффы).
- Что подтверждено какими эталонами и какие правила формата сняты с Designer: `golden/manifest.yaml`.
- Профили и блокирующие коды: `references/validation-profiles.yaml`; норматив
  правил — `scripts/sherpa_xml/validator.py` (коды SEM-*/SHP-*).

## Правила генерации (из ревью реальной задачи; эталон — `golden/real/best-screenshots-mail-save` в репозитории скила; в runtime-копию golden-файлы не ставятся — см. `golden/manifest.yaml`)

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
   списка — `ForeachList`. Различать итерации («третий — почтой, четвёртый и
   пятый — на диск») → If по `$Index` в теле (`$Index -eq 3`, `$Index -ge 4`),
   а не отдельные ветки снаружи. `WhileLoop` — только если условие не
   выражается диапазоном.
6. **DisplayName — смысловой**: условие → текст условия; ToDo → действие
   (п.3). Задаётся узлом `display_name` (для ToDo берётся из unresolved_action).
7. **Не выдумывай значения.** Адреса, порты, пути, которые задача не дала, —
   создавай переменные-заготовки ПУСТЫМИ (Text '', Number 0, SecureString ''),
   оператор заполнит. Не подставляй `company.local` и подобное.
8. **Обработка ошибок — свойство блока.** У большинства блоков есть `ErrorLevel`,
   `ErrorDirection`, `ErrorMessage`, `ReportLevel`; статический выход `Error`
   есть не у всех (у `While` и `IfCondition` его нет — см. `golden/manifest.yaml`).
   `TryCatch` нужен для обёртки группы, а не как единственный способ обработки.
   В плане ошибка блока выражается его выходом: `"on_error": [шаги]` у `action`.

Правила 3, 4, 7 проверяются машинно (`validate-ir`, WARN `SHP-GEN-002/001/003/004`):
длинный `unresolved_action`, свойство-дефолт, выдуманный адрес, секрет в значении.
Остальные правила — на совести модели: в отчёте видно только проверенное.

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
3. **Блоки** — по глаголу из задачи: `python scripts/block_suggest.py "отправить письмо"`
   даёт кандидатов с частотами по корпусу (индекс `assets/verb_block_index.json`);
   решение — по каталогу `assets/block_index.json` и тексту задачи. Нет блока → `ToDo`.
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
4. **Переименование переменной — операцией `rename_variables`**, не текстовой
   заменой: `apply_spec.py` применяет семантику рефакторинга Designer
   (IL-verified: вне кавычек `"`/`'`, вне зон `#stoprefactoring…#startrefactoring`,
   `${имя}` остаётся фигурной, `$env:`-квалификаторы не трогаются) к значениям
   свойств всех блоков и объявлению в `Properties`; `change_impact.py` заранее
   показывает, где заменится, а где Designer закономерно НЕ тронет (кавычки/
   директивы — эти ссылки на старое имя останутся). Вхождения в кавычках —
   поведение инструмента, а не пропуск правки.
5. **Правка — спецификацией, не руками**: та же JSON-спецификация, по которой
   считался радиус, применяется `python scripts/apply_spec.py FILE.process
   --spec spec.json --backup`. Инструмент клонирует новые блоки из родных блоков
   этого же файла (`Code`, `Version`, порты, порядок атрибутов), сохраняет BOM и
   переводы строк, перед записью сверяет дифф со спецификацией и отказывается
   писать, если тот разошёлся или появились новые дефекты. Бэкап обязателен.
6. **Проверка**: `python scripts/process_diff.py OLD NEW --expect spec.json` — код
   возврата 1, если дифф содержит не ровно то, что в спецификации;
   `structure_audit.py` до/после (не выросли ли `goto-*`, хабы, мёртвые блоки);
   инварианты поведения. В отчёте — раздел «блоки не менялись, но изменили смысл».
   Байтовая идентичность ≠ неизменность поведения: это два разных отчёта.

## Ревью чужого сценария (ничего не меняем)

Порядок и чек-лист — `references/reviewing-a-process.md`; здесь только маршрут.

1. `python scripts/lint_process.py FILE.process` (то же: `scripts/cli.py lint-process FILE`)
   — объективные дефекты с P0/P1/P2: секреты в открытом виде (P0), висячие связи и
   дубли ID (P0), обрывы потока и `ErrorLevel=Handle` без выхода `Error` (P1),
   выключенные блоки, мёртвый код, блоки вне каталога и старые версии,
   несбалансированные `#stoprefactoring`, остатки автосейвов `~<имя>` (P2).
   Секреты в отчёт не перепечатываются — только класс и место.
2. `python scripts/structure_audit.py FILE.process` — где управление держится на
   GOTO: `AbortContainer`, стрелки назад, хабы, горячие переменные.
3. `python scripts/golden_inspector.py FILE --deep` — только для спорных блоков.
4. Смысл (промпт ↔ схема разбора, единицы, контракт робота) читается глазами: это
   единственное, что машинно не проверяется. Выводы, зависящие от рантайма, — в
   раздел «что не проверялось», а не в факты.
5. Ревью и правка не смешиваются: найденное идёт в отчёт, правка — отдельным
   сценарием выше (`change_impact` → `apply_spec` → `process_diff --expect`).

## Расширение базы

- **Каталог**: `python scripts/dev/harvest_catalog.py` (после обновления Designer).
  Существующие golden-шаблоны сохраняются как есть (`--no-preserve` — заменить все).
- **Корпусный индекс глаголов**: `python scripts/dev/build_verb_index.py` —
  пересобрать `assets/verb_block_index.json` после обновления корпуса
  (golden/real + проект ревью + установка Designer).
- **Инспектор чужого .process** (рантайм, только stdlib): `scripts/golden_inspector.py FILE --deep`.
- **Приёмка после изменений**: `harvest_catalog.py --check` = 752/752 (включая
  50 golden), `replay_corpus.py` = 0 mismatch (988 exact + 42 legacy-code;
  legacy-code допустим), затем `cli.py build` демо-IR и eval-пары:
  `python -m pytest tests/unit/test_evals.py` (план → IR → build → `graph_compare`
  против эталонов: Designer-артефакт, замороженная сборка, верхний уровень OCR).

## Известные границы (не расширяй молча)

- Dictionary-переменные не генерируются: контейнер не подтверждён golden
  (SEM-VAR-010; временно — Hashtable).
- SecureString объявляется **только пустым** (SEM-VAR-011): непустое начальное
  значение компилятор не собирает — секрет задаётся через `Assign` + `_tosecure`.
- Непустые начальные значения Object/Money не подтверждены — объявляйте пустыми
  и присваивайте через Assign.
- Вложенные Hashtable/DataTable в ячейках таблиц — вне MVP.
- 6 блоков есть в движке, но не в палитре (`LaunchBrowserWithUrl`, `SetToUIText`,
  `SaveWordDocumentToPath`, `SaveExcelDocumentToPath`, `ReplaceTextInsideWordDocumentWithText`,
  `AddTextToParagraphWordDocument`) — генерируются, но в палитре Designer их не видно.
- Блоков, удалённых/переименованных в 1.4.4, в каталоге нет: `LaunchChrome`→`LaunchBrowser`,
  `LaunchIE`→`LaunchBrowser`, `RecognizeCaptcha`→`RecognizeCaptchaOCR`,
  `InternalBrowserGet`→`InternalBrowserAsyncGet`, `NewFile`→`OrchStorageCreateFile`.
  Старые процессы с ними не воспроизводятся.
- `PathStyle`/`PathStyle_DragThumb` в шаблоны не встраиваются (как и в 50 golden):
  в XAML это ссылки `{StaticResource …}`. Если Designer начнёт требовать inline-стили —
  это отдельная фаза, менять только с проверкой загрузки.
- Автосейвы Designer (`~<имя>.process`, скрытые, пишет `AutoSaveProcessed`) —
  не авторский контент: корпус и ревью их не читают (LNT-BAK-001 в lint),
  `apply_spec` предупреждает о несинхронизированном остатке рядом с правимым
  файлом.
- `rename_variables` не трогает `DisplayName`/`Description`, `DefaultValue`
  свойств и корневые `InputArguments`/`OutputArguments` (тракт аргументов не
  разбирался; при непустых — проверить руками). Ссылки на старое имя внутри
  кавычек и зон `#stoprefactoring` остаются — так делает и сам Designer.
- Для новых версий Designer — перегенерировать каталог; версии блоков плавают
  (в корпусе 1469 инстансов старее текущего каталога — это нормально).
