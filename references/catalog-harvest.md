# Каталог блоков: как снимается, что подтверждает, где границы

Каталог не написан руками — он **снимается с установленного Designer**. Источник истины
для генерации: `assets/block_templates.json` (шаблоны `Content`) + `assets/block_index.json`
(индекс). `references/block-catalog.yaml` — сгенерированный человекочитаемый справочник.

## Источники на диске

| Файл | Что даёт | Ограничение |
|---|---|---|
| `<APP>\Designer\ExternalToolboxes\MainToolbox.xaml` (5,4 МБ) | **752 блока**: `<core:ToolboxItemSettings.Code>` (PowerShell-шаблон), `Version`, `Width/Height`, `PathToImage`, `LinuxAvailable`, свойства (`Name/Tooltip/CatalogName/Type/Value/ValueType/EnumVariants/Caption`), порты (`Name/Orientation/MaxIn/MaxOut`) | подписи английские, `IsRequired` отсутствует |
| `<APP>\Designer\ExternalToolboxes\Toolboxes-<lang>.json` (3,6 МБ) | **746 блоков** палитры: локализованные `DisplayName`/`FriendlyName`/`Tooltip`, группа | нет кода и версий |
| корпус `*.process` (replay по `%APPDATA%`: 208 файлов) | реальные значения свойств, версии, связи | 1469 инстансов старее текущего каталога |

## Конвейер

```text
MainToolbox.xaml ─┐
Toolboxes-ru.json ┴─► harvest_catalog.py ─┬─► assets/block_templates.json (752)
                                          ├─► assets/block_index.json    (752)
                                          └─► references/block-catalog.yaml
                                                    │
                                     harvest_catalog.py --check  (752/752; из них 50 golden)
                                     replay_corpus.py            (реальные инстансы)
```

```bash
python scripts/dev/harvest_catalog.py            # собрать/обновить
python scripts/dev/harvest_catalog.py --check    # сверить с golden-набором
python scripts/dev/replay_corpus.py --report replay.json
```

## Типы свойств: `@Type` и `@ValueType` — разные таксономии

- `@Type` (9 непустых значений): `Expression`, `Variable`, `Enum`, `Bool`, `Selector`,
  `Image`, `EnumMulti`, `String`, `Double` — это грамматические классы strict-проверки.
- `@ValueType` (76 значений в текущем каталоге): `Text`, `Number`, `ExcelInstance`,
  `EmailItem`, `Selector`, `DataTable`, … — это типы значений/редакторов Designer,
  не путать с `@Type`.
- У 4 свойств `@Type` пустой; каталог трактует их как `Expression`.

## Правила сборки шаблона (проверены на 50 эталонах)

- `<Property Name="…">` — канонический вид свойства (в корпусе, снятом на момент
  каталога — 211 `.process`, 24 194 свойства — **ни одного** именованного элемента;
  `ET` подтверждает тег `Property`).
- `<ConnectorSettings Name="…">` — нормализованная запись портов; так же записаны
  50 golden-шаблонов, на этом построен `compiler._parse_ports`.
- `IsRequired` = `True` при `CatalogName="Required"` (в XAML этого поля нет).
- `EnumVariants`/`ValueType`: `""` и `{x:Null}` — один и тот же «пустой» sentinel
  (в 50 эталонах 167 против 142), генерируем `""`.
- Новые строки в атрибутах — только сущностями: CR → `&#xD;`, LF → `&#xA;`
  (иначе XML-нормализация атрибутов съедает перевод строки; найдено на многострочном
  `Comment` из `InitAllSettings.process`).
- 50 исходных шаблонов сохраняются дословно (`--no-preserve` перегенерирует все).

## Метрики приёмки (сняты на этой установке)

| Проверка | Результат |
|---|---|
| `harvest_catalog.py --check` | **752/752**, 0 diff; из них 50 golden — по version, display, size, code, портам, метаданным свойств; `block_index` не stale |
| `replay_corpus.py` — инстансы актуальных версий | **988 exact + 42 legacy-code, 0 mismatch** из 1030 |
| replay: инстансы старых версий | 1469 — ожидаемо (каталог 1.4.4 новее) |
| replay: блоки без шаблона | 30 — блоки, удалённые/переименованные в 1.4.4 |
| replay: всего инстансов | 2529 |
| связей `InputLeft` в корпусе | 326 — воспроизводится `python scripts/dev/corpus_stats.py`; зависит от установленных проектов |

«legacy-code» = версия блока совпадает, но сохранённый код старше текущей генерации
(в старых сборках блоки писались без `try {`-обёртки). Шаблон при этом верный.

## Удалённые/переименованные блоки (встречаются в примерах, но не в 1.4.4)

| Старый блок | Замена в 1.4.4 |
|---|---|
| `LaunchChrome` (25 инстансов в примерах) | `LaunchBrowser` |
| `LaunchIE` | `LaunchBrowser` |
| `RecognizeCaptcha` | `RecognizeCaptchaOCR` |
| `InternalBrowserGet` | `InternalBrowserAsyncGet` / `InternalBrowserRequest` |
| `NewFile` | `OrchStorageCreateFile` (иное: Storage Оркестратора) |

## Открытые вопросы (закрыты данными)

1. **Имя элемента свойства** → `<Property Name="…">` (0 именованных на 24 194).
2. **Base64 для Text** → все непустые значения `DiagramProperty Type=Text` лежат в
   контейнере (на момент снятия — 260/260; в текущем replay 126/126); payload —
   PS-литерал `"…"` (252) либо «сырые» байты (4: путь, `A`, GUID-токен, пустая
   строка). Поэтому в `types/text.py` есть строгий `decode` и толерантный
   `decode_lenient`.
3. **`IsGlobal`** → атрибут пишется всегда (`True`/`False`); в 26 старых файлах его
   нет вовсе (93 переменные) — это legacy-формат, семантика = локальная. Генератор
   всегда пишет атрибут явно.
4. **`MaxIn=100`** → маркер «не ограничено»: у 742 блоков `Input` и 738 `InputLeft`
   ровно 100, но у 5 блоков `InputLeft` = 1; наблюдаемый максимум входа — 9 (`Input`),
   6 (`InputLeft`). Валидатор трактует значение буквально и не хардкодит 100.
5. **6 блоков вне палитры** → это варианты существующих (`LaunchBrowserWithUrl` →
   `LaunchBrowser` и т.п.), `Invisible=False`, `NoDelete=False`. Генерируются, в
   палитре не видны; для пользовательских сценариев предпочтительны палитровые.
