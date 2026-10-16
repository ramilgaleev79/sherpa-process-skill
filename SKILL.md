---
name: sherpa-process
description: Генерация, валидация и инспекция .process XML для Sherpa RPA Designer 1.4.4. Использовать всегда, когда пользователь просит создать процесс/сценарий/робота для Sherpa RPA, сгенерировать или проверить .process файл, собрать процесс из описания, добавить блоки (Assign, Условие, Цикл Пока/Для, Try/Catch, пауза, коллекции, DataTable, переменные), модифицировать IR процесса, или разобраться со структурой/сериализацией .process. Триггерится на любые упоминания Sherpa RPA, Sherpa Designer, .process, .sherpa, Sherpa-процесс, сгенерировать робота/диаграмму.
---

# Sherpa Process Skill

Детерминированная генерация `.process` для Sherpa RPA Designer 1.4.4 из IR (JSON).
Каталожная база: 61 блок / 64 версии / 8 типов переменных — всё подтверждено
24 golden-эталонами (`golden/manifest.yaml` — карта всех подтверждений).

## Железные принципы

1. **Ты никогда не пишешь .process XML руками.** Только IR (JSON); XML генерирует
   `scripts/cli.py build` из golden-шаблонов. Не декодируй Base64-контейнеры в уме —
   для этого инспектор.
2. **Confirmed-over-invented.** Нет блока/типа/операции в `ir.NODE_TYPES` — значит
   не подтверждено: ставь узел `ToDo` с `unresolved_action` (draft) и предложи
   golden-образец. Никогда не подставляй «похожий» блок.
3. **Детерминизм.** Один IR → байт-в-байт один файл (uuid5-ID, фикс. порядок).
   `build` сам проверяет это и падает при нарушении.
4. **Профили**: draft (ToDo разрешён, предупреждения) / release (ToDo и
   необъявленные ссылки блокируют выпуск) / strict (+типовая строгость выражений,
   freeform PowerShell запрещён).

## Рабочий цикл

```bash
# 1. Шаблон IR с примерами всех узлов
python scripts/cli.py init-ir my.ir.json

# 2. Заполни IR (см. контракт ниже), затем валидация под профиль
python scripts/cli.py validate-ir my.ir.json --profile release

# 3. Сборка .process (валидация профиля + детерминизм-чек встроены)
python scripts/cli.py build my.ir.json my.process --mode release
```

Python 3.10+, без зависимостей. dev-инструменты (`scripts/dev/`: инспектор golden,
генератор каталогов, ingest, extract_pattern, Schematron-прогон) требуют репозиторий
скила с venv (saxonche/pyyaml) — в установленной копии рантайма их не используй.

## Контракт IR (кратко; полный — scripts/sherpa_xml/ir.py, ошибки подскажут)

- `process{name, mode}`, `variables[{key,name,sherpa_type,scope,initial}]`,
  `nodes[{key,type,…}]`, `edges[{from,from_port,to,to_port}]`.
- Типы переменных: Number, Logical, Text, SecureString, Hashtable, Dictionary, List, DataTable.
- `initial`: number/boolean/string_literal — прямо; коллекции —
  `list_literal`/`hashtable_literal`/`data_table_literal{columns,rows}`.
- Узлы: Start, Stop, ToDo, Assign(assignments), Condition(condition,branches),
  WhileLoop/ForLoop, TryCatch, Container, AbortProcess, AbortContainer,
  InvokeExpression, Delay + List*/Table*/Collection* (полный список — ir.NODE_TYPES).
- Строки в IR — сырые (без PS-кавычек/экранирования — их ставит компилятор).
- `external_variables[{name,expected_type}]` глушат SEM-REF-001 для входов робота.

## Справочники (читай по требованию)

- Сериализация Text/контейнеров: `references/text-serialization.md`,
  `references/datatable-serialization.md`.
- Каталоги блоков/типов (порты, свойства, версии, дефолты):
  `references/block-catalog.yaml`, `references/type-catalog.yaml`.
- Профили: `references/validation-profiles.yaml`; правила: `schemas/sherpa-rules.sch`.
- Канонический мастер-контекст: `main.md`; статус проекта: `PLAN.md`.

## Правила генерации (из ревью реальной задачи; эталон — golden/real/best-screenshots-mail-save)

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
   БЕЗ переменной-счётчика и инкремента — паттерн for-counter). Различать
   итерации («третий — почтой, остальные — на диск») → If по `$Index` в теле
   (условие вида `$Index -gt 2`), а не отдельные ветки снаружи. `WhileLoop` —
   только если условие не выражается диапазоном.
6. **DisplayName — смысловой**: условие → текст условия; ToDo → действие
   (п.3). Задаётся узлом `display_name` (для ToDo берётся из unresolved_action).
7. **Не выдумывай значения.** Адреса, порты, пути, которые задача не дала, —
   создавай переменные-заготовки ПУСТЫМИ (Text '', Number 0, SecureString ''),
   оператор заполнит. Не подставляй `company.local` и подобное.

## Эталонные паттерны (patterns/*/pattern.json, status=confirmed)

Проверенные golden-ом конструкции: `for-counter` (фиксированные повторы — For 1..N
без счётчика + If по $Index для различения итераций), `while-counter` (цикл по
не-диапазонному условию), `retry-trycatch` (повторы с FullAbort).
Когда логика запроса совпадает — прочитай паттерн, вставь `fragment` в IR
(уникальные ключи, стыковка по meta.entry/exit/body_slot), параметры-переменные
переименуй под контекст. DRAFT-паттерны не использовать.

## Расширение базы (Extension Pipeline)

- **Новый блок**: пользователь принёс .process → в репозитории скила:
  `scripts/dev/ingest_block.py FILE.process [golden-имя]` → скрипт скопирует в
  golden, перегенерирует каталоги и покажет строку для `ir.NODE_TYPES`
  (логическое имя выбираешь ты; потом pytest). Мимо golden блок не проходит.
- **Новый паттерн**: `scripts/dev/extract_pattern.py FILE.process NAME [ID-префиксы]`
  → raw-черновик → нормализуй в IR → status=confirmed → тесты.
- **Инспектор чужого .process**: `scripts/dev/golden_inspector.py FILE --deep`.

## Известные границы (не расширяй молча)

- Dictionary: начальное значение не генерируется (контейнер не подтверждён).
- Hashtable*-блоки операций — без golden (release блокирует).
- Вложенные Hashtable/DataTable в ячейках таблиц — вне MVP.
- Для новых версий Designer — переснять golden-серию заново (версии блоков плавают).
