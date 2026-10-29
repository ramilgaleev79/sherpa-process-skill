# Запрос golden-эталонов из Sherpa Designer

Это чек-лист заявки на новый golden-эталон. Каждый файл закрывает конкретный пробел знаний.
Файлы складывать в `golden/`, рядом заполнить `golden/manifest.yaml` (шаблон в конце).

## Общие правила

1. **Зафиксировать точную версию Sherpa Designer** (Help → О программе) — записать в manifest первым пунктом. Все выводы скила привязаны к версии.
2. Каждый процесс создавать **с нуля в новом файле**, сохранять один раз, не переоткрывать и не пересохранять.
3. Не менять layout руками (не двигать блоки) — иначе в XML попадут случайные координаты; для фазы 1 нужны «чистые» дефолтные позиции.
4. Имена переменных и текстовые значения — точно как указано (сравниваем побайтово).
5. Для каждой пары файлов серии A — записать в manifest **единственное отличие** между соседними файлами (это основа дифф-метода).

## Серия A — диагностическая (обязательная, закрывает структуру и граф)

| Файл | Что создать в Designer |
|------|------------------------|
| `A1-start-stop.process` | Пустой процесс: Start → Stop (только стандартные начало и конец) |
| `A2-assign.process` | Start → Assign (одна пара: `Counter = 10`) → Stop |
| `A3-assign-x2.process` | Start → Assign (`Counter = 10`) → Assign (`Counter = 20`) → Stop |
| `A4-condition.process` | Переменная `Enabled` (Logical, True). Start → Условие (`$Enabled`) → Yes: Stop, No: Stop |
| `A5-variables.process` | Без блоков кроме Start → Stop. Переменные: `Num` (Number, 10), `Flag` (Logical, True), `Txt` (Text, `Test123`), `Sec` (SecureString, пустой) |
| `A6-collections.process` | Без блоков кроме Start → Stop. Переменные: `Ht` (Hashtable, пустая), `Dict` (Dictionary, пустой), `Lst` (List, пустой) |
| `A7-while-empty.process` | Start → Цикл Пока (условие НЕ заполнять) → тело: пусто → Next: Stop |

Серия A закрывает: физическое кодирование рёбер графа между блоками, шаблоны Start/Stop (их InternalName и версии), координаты layout в дефолтном виде, порядок секций корня, пустой шаблон While.

## Серия B — приоритетная (закрывает сериализации и поведение)

| Файл | Что создать | Закрывает пробел |
|------|-------------|------------------|
| `B1-while-filled.process` | `Counter` (Number, 0). Start → Пока (`$Counter -lt 3`) → тело: Assign (`Counter = $Counter + 1`) → Next: Stop | Заполненное While-условие (P1) |
| `B2-list-values.process` | Переменная `Lst` (List) с начальными значениями `One`, `Two`, `Three` в декларации. Start → Stop | Сериализатор начального значения List (P2) |
| `B3-nested-cells.process` | DataTable `Tbl`: колонка `Obj` типа Hashtable с одним непустым значением; колонка `Inner` типа DataTable с одним непустым значением | Вложенные ячейки DataTable (P3) |
| `B4-condition-compound.process` | `Number` (Number, 10), `Text` (Text, `Test`). Условие: `($Number -gt 5) -and ($Text -eq "Test")`, Yes/No → Stop | Составное условие IfElse (P4) |
| `B5-rare-columns.process` | DataTable с колонками редких типов: TimeSpan, Char, Single, Byte, Int16, Int64, UInt16, UInt32, UInt64, SByte — по одной строке со значением | Редкие типы колонок (P5) |
| `B6-hashtable-filled.process` | `Ht` (Hashtable) с 2–3 парами ключ-значение в начальном значении. Start → Stop | Начальное значение Hashtable в декларации |

Если что-то из серии B невозможно создать в вашей версии Designer (нет типа колонки и т.п.) — отметьте это в manifest, это тоже результат.

## Шаблон `golden/manifest.yaml`

```yaml
designer_version: "1.XX.X"          # точная версия из О программе
created: "2026-10-XX"
notes: ""
files:
  - file: A1-start-stop.process
    confirms: минимальный граф Start→Stop, шаблоны блоков, дефолтный layout
    diff_to_previous: null
  - file: A2-assign.process
    confirms: блок Assign с одной парой Variable1/Expression1
    diff_to_previous: "+ блок Assign между Start и Stop"
  # ... остальные файлы
impossible:
  - file: B5-rare-columns.process
    reason: "тип колонки SByte недоступен в интерфейсе"
```

## Что с этим будет дальше

Файлы анализируются локально: `scripts/dev/golden_inspector.py` декодирует Base64-контейнеры и фиксирует побайтовую структуру (включая PREFIX текстового контейнера, который сейчас не известен). Секретов в файлах быть не должно — все значения тестовые. Прогон `schemas/sherpa-rules.sch` по этим файлам заодно проверит сами правила.
