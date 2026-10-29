"""Спецификация правки `.process` — один контракт для трёх инструментов.

Одна и та же JSON-спецификация обслуживает весь цикл правки:

    change_impact.py --spec spec.json FILE      # радиус ДО записи (в памяти)
    apply_spec.py     --spec spec.json FILE     # запись ФАЙЛА по спецификации
    process_diff.py   --expect spec.json OLD NEW  # доказательство ПОСЛЕ записи

Именно общий контракт закрывает главный риск: «считали одно, записали другое».

Схема (все ключи, кроме перечисленных в элементах, необязательны):

    {
      "add_blocks":    [{"id": "<guid>", "internal": "SendMailMessage",
                         "display": "…", "description": "…",
                         "clone_of": "<guid родного блока этого файла>",
                         "props": {"Subject": "\"…\""},
                         "place": {"anchor": "<guid>", "left": 100, "top": 200}}],
      "remove_blocks": ["<guid>"],
      "add_edges":     [["<src>", "Next", "<dst>", "Input"]],
      "remove_edges":  [["<src>", "Next", "<dst>", "Input"]],   // null = любой
      "set_props":     [{"block": "<guid>", "name": "FileName", "value": "…"}],
      "rename_variables": [{"from": "$old", "to": "new"}]
    }

`rename_variables` — переименование переменной «как Designer»: по семантике
`ClassRefactoringExpression125` (вне кавычек, вне `#stoprefactoring`-зон,
`${имя}` остаётся фигурной, `$env:`-квалификаторы не трогаются —
`sherpa_xml/designer_refactor.py`, IL-verified). Меняются объявление в
`Properties` и значения свойств всех блоков; вхождения в кавычках и зонах
директив НЕ переименовываются — это поведение инструмента, а не пропуск
правки (change_impact показывает их отдельно).

`rename_variables` можно комбинировать с остальными операциями; порядок
применения в `apply_spec`: сначала переименование (значения свойств всех
блоков + объявления), затем `set_props` поверх переименованной базы —
то есть `set_props` на том же свойстве **перекрывает** рефакторинг, клоны
(`add_blocks`) наследуют уже переименованные значения прототипов, а блоки
из `remove_blocks` не рефакторятся. Переименованию НЕ подвергаются:
`DisplayName`/`Description` (не выражения), `DefaultValue` (шаблон блока),
корневые `InputArguments`/`OutputArguments` (тракт не разбирался — при
непустых аргументах робота проверьте их руками).

`set_props.value = null` — «снять переопределение»: в файле значение возвращается
к `DefaultValue` свойства (удалить элемент Property нельзя, он часть шаблона).

Два уровня экранирования
------------------------
`Content` в `.process` — экранированный XAML в текстовом узле. Значение свойства
в спецификации задаётся как сырая PS-строка (как `properties` в IR):

    raw                  →  escape_inner  →  escape_content  →  файл
    \"Письмо\"           →  &quot;Письмо&quot;  →  &amp;quot;…    →  Value="&amp;quot;…"

`process_diff` разбирает `Content` до конца (два разбора), поэтому видит ровно
сырое значение `\"Письмо\"` — сверка сравнивает спецификацию с ним напрямую.
Экранирование нужно только при записи (`apply_spec.py`). Два уровня — это
сущности (`&`/`<`/`>`/`"`); кавычка в каждом из трёх состояний текста (raw /
XAML-атрибут / файл) записывается по-своему — эти пространства кавычек
описаны в `sherpa_xml/designer_refactor.py` (QUOTE_*); `rename_variables`
работает прямо в файловом пространстве, поэтому не проходит через
экранирование вовсе.

Только stdlib; ошибки — `SpecError` с машинным текстом.
"""

from __future__ import annotations

SPEC_KEYS = frozenset({"add_blocks", "remove_blocks", "add_edges", "remove_edges",
                       "set_props", "rename_variables"})
ADD_BLOCK_KEYS = frozenset({"id", "internal", "display", "description", "props", "clone_of", "place"})
PLACE_KEYS = frozenset({"anchor", "left", "top"})
EDGE_KEYS = ("src", "port", "dst", "sink")
RENAME_KEYS = frozenset({"from", "to"})

# Свойства, значение которых в отчёт не печатается: отчёт читают люди и копируют
# в задачи/письма, а секрет в отчёте снова становится утечкой.
SECRET_PROP_NAMES = frozenset({
    "password", "passwd", "pwd", "token", "apikey", "api_key", "secret",
    "credentials", "privatekey", "private_key", "пароль", "секрет", "токен",
})


def is_secret_name(name: str) -> bool:
    low = name.lower()
    return low in SECRET_PROP_NAMES or "password" in low or "secret" in low or "пароль" in low


def mask_value(name: str, value: object) -> str:
    """Значение для отчёта: у секретных свойств — только факт «есть», не текст."""
    if value is None:
        return "—"
    if is_secret_name(name):
        return "«секрет скрыт»"
    text = str(value).replace("\n", " ")
    return text[:100] + ("…" if len(text) > 100 else "")


class SpecError(ValueError):
    """Спецификация правки не соответствует схеме — применять/считать нельзя."""


# --- экранирование -----------------------------------------------------------

def escape_inner(raw: str) -> str:
    """Значение свойства → вид внутри разобранного `Content` (один уровень)."""
    return (raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def escape_content(raw: str) -> str:
    """Значение свойства → вид в тексте файла (два уровня: XAML внутри Content)."""
    return escape_inner(escape_inner(raw))


def escape_tag_text(raw: str) -> str:
    """Текст обычного элемента (`Description`) — только символы текстового узла."""
    return raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- нормализация ------------------------------------------------------------

def edge_list(value: object, where: str, *, partial: bool) -> list[tuple]:
    """Элемент ребра → кортеж длиной 4 (недостающие поля — None)."""
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise SpecError(f"{where}: ожидался список [src, port, dst, sink] "
                        f"(для remove_edges любое поле можно null)")
    if not partial and len(value) != 4:
        raise SpecError(f"{where}: ожидалось ровно 4 поля [src, port, dst, sink]")
    return tuple(list(value) + [None] * 4)[:4]


def normalize(spec: dict, *, partial_edges: bool = True) -> dict:
    """Приводит спеку к полному виду: все шесть списков и кортежи рёбер.

    partial_edges=True — `remove_edges` допускает null (как в change_impact).
    rename_variables нормализует имена (`$x`/`${x}`/`x` → `x`).
    """
    from sherpa_xml.designer_refactor import normalize_var_name

    renames = []
    for op in spec.get("rename_variables") or []:
        renames.append({"from": normalize_var_name(op["from"]),
                        "to": normalize_var_name(op["to"])})
    out = {
        "add_blocks": list(spec.get("add_blocks") or []),
        "remove_blocks": list(spec.get("remove_blocks") or []),
        "add_edges": [edge_list(x, "add_edges", partial=False) for x in spec.get("add_edges") or []],
        "remove_edges": [edge_list(x, "remove_edges", partial=partial_edges)
                         for x in spec.get("remove_edges") or []],
        "set_props": list(spec.get("set_props") or []),
        "rename_variables": renames,
    }
    return out


def rename_mapping(spec: dict) -> dict[str, str]:
    """mapping {старое: новое} для всех rename_variables (имена без `$`)."""
    return {op["from"]: op["to"] for op in normalize(spec)["rename_variables"]}


def is_empty(spec: dict) -> bool:
    """Пустая спецификация = «в файле не должно быть никаких отличий»."""
    return not any(spec.get(k) for k in SPEC_KEYS)


# --- проверка схемы ----------------------------------------------------------

def validate_spec(spec: dict, blocks: dict) -> None:
    """Строгая проверка спецификации ДО расчёта/записи.

    Инструмент — предохранитель перед правкой, поэтому опечатка в ключе или
    несуществующий ID должны быть ошибкой, а не «локальной правкой» с exit 0.
    blocks — {id: мета блока} из structure_audit.load (или process_diff.load).
    """
    if not isinstance(spec, dict):
        raise SpecError("спецификация должна быть JSON-объектом")
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise SpecError(f"неизвестные ключи спецификации: {sorted(unknown)}; допустимо {sorted(SPEC_KEYS)}")
    for key in SPEC_KEYS:
        if key in spec and not isinstance(spec[key], list):
            raise SpecError(f"{key} должен быть списком")

    added_ids: set[str] = set()
    for nb in spec.get("add_blocks") or []:
        if not isinstance(nb, dict):
            raise SpecError("add_blocks: элемент должен быть объектом")
        unknown_fields = set(nb) - ADD_BLOCK_KEYS
        if unknown_fields:
            raise SpecError(f"add_blocks: неизвестные поля {sorted(unknown_fields)}; "
                            f"допустимо {sorted(ADD_BLOCK_KEYS)}")
        bid = nb.get("id")
        if not isinstance(bid, str) or not bid:
            raise SpecError("add_blocks: у блока должен быть непустой строковый id")
        if bid in blocks or bid in added_ids:
            raise SpecError(f"add_blocks: id {bid!r} уже существует")
        if not isinstance(nb.get("internal"), str) or not nb["internal"]:
            raise SpecError(f"add_blocks {bid}: нужен строковый internal")
        for text_field in ("display", "description"):
            if nb.get(text_field) is not None and not isinstance(nb[text_field], str):
                raise SpecError(f"add_blocks {bid}: {text_field} должен быть строкой")
        props = nb.get("props") or {}
        if not isinstance(props, dict) or not all(isinstance(v, str) for v in props.values()):
            raise SpecError(f"add_blocks {bid}: props должен быть объектом строк")
        clone_of = nb.get("clone_of")
        if clone_of is not None:
            if not isinstance(clone_of, str) or clone_of not in blocks:
                raise SpecError(f"add_blocks {bid}: clone_of должен быть id блока этого файла")
            if clone_of in (spec.get("remove_blocks") or []):
                raise SpecError(f"add_blocks {bid}: clone_of указывает на удаляемый блок")
        place = nb.get("place")
        if place is not None:
            if not isinstance(place, dict):
                raise SpecError(f"add_blocks {bid}: place должен быть объектом")
            extra = set(place) - PLACE_KEYS
            if extra:
                raise SpecError(f"add_blocks {bid}: в place неизвестные поля {sorted(extra)}")
            anchor = place.get("anchor")
            if anchor is not None and (not isinstance(anchor, str) or anchor not in blocks):
                raise SpecError(f"add_blocks {bid}: place.anchor должен быть id блока этого файла")
            for side in ("left", "top"):
                if place.get(side) is not None and not isinstance(place[side], (int, float)):
                    raise SpecError(f"add_blocks {bid}: place.{side} должен быть числом")
        added_ids.add(bid)

    known = set(blocks) | added_ids
    removed: set[str] = set()
    for bid in spec.get("remove_blocks") or []:
        if not isinstance(bid, str) or bid not in blocks:
            raise SpecError(f"remove_blocks: нет блока {bid!r}")
        if bid in added_ids:
            raise SpecError(f"remove_blocks: блок {bid!r} добавляется этой же правкой")
        removed.add(bid)

    def _endpoint(where: str, value: object, *, nullable: bool) -> None:
        if value is None:
            if not nullable:
                raise SpecError(f"{where}: null не допускается")
            return
        if not isinstance(value, str) or value not in known:
            raise SpecError(f"{where}: неизвестный блок {value!r}")

    seen_edges_del: set[tuple] = set()
    for x in spec.get("remove_edges") or []:
        src, port, dst, sink = edge_list(x, "remove_edges", partial=True)
        _endpoint("remove_edges.src", src, nullable=True)
        _endpoint("remove_edges.dst", dst, nullable=True)
        for name, value in (("port", port), ("sink", sink)):
            if value is not None and not isinstance(value, str):
                raise SpecError(f"remove_edges.{name} должен быть строкой или null")
        if (src, port, dst, sink) in seen_edges_del:
            raise SpecError(f"remove_edges: удаление связи {[src, port, dst, sink]} задано дважды")
        seen_edges_del.add((src, port, dst, sink))

    seen_edges_add: set[tuple] = set()
    for x in spec.get("add_edges") or []:
        src, port, dst, sink = edge_list(x, "add_edges", partial=False)
        _endpoint("add_edges.src", src, nullable=False)
        _endpoint("add_edges.dst", dst, nullable=False)
        if not isinstance(port, str) or not isinstance(sink, str):
            raise SpecError("add_edges: port и sink должны быть строками")
        if src in removed or dst in removed:
            raise SpecError("add_edges: нельзя добавить связь к удаляемому блоку")
        if (src, port, dst, sink) in seen_edges_add:
            raise SpecError(f"add_edges: связь {src[:8]} --{port}--> {dst[:8]} задана дважды")
        seen_edges_add.add((src, port, dst, sink))

    seen_props: set[tuple[str, str]] = set()
    for sp in spec.get("set_props") or []:
        if not isinstance(sp, dict):
            raise SpecError("set_props: элемент должен быть объектом")
        bid, name = sp.get("block"), sp.get("name")
        if not isinstance(bid, str) or bid not in known:
            raise SpecError(f"set_props: неизвестный блок {bid!r}")
        if bid in added_ids:
            raise SpecError(f"set_props: блок {bid[:8]} добавляется этой же правкой — "
                            f"задайте свойство в add_blocks.props (set_props меняет "
                            f"существующие блоки)")
        if bid in removed:
            raise SpecError(f"set_props: блок {bid!r} удаляется этой же правкой")
        if not isinstance(name, str) or not name:
            raise SpecError("set_props: нужно непустое имя свойства")
        value = sp.get("value")
        if value is not None and not isinstance(value, str):
            raise SpecError("set_props.value должен быть строкой или null")
        if (bid, name) in seen_props:
            raise SpecError(f"set_props: свойство {name} блока {bid[:8]} задано дважды")
        seen_props.add((bid, name))

    # rename_variables: имена нормализуем и проверяем однократность
    from sherpa_xml.designer_refactor import normalize_var_name

    raw_renames = spec.get("rename_variables") or []
    for op in raw_renames:
        if not isinstance(op, dict) or set(op) - RENAME_KEYS or not RENAME_KEYS <= set(op):
            raise SpecError("rename_variables: элемент должен быть объектом "
                            f"с полями {sorted(RENAME_KEYS)}")
        if not isinstance(op["from"], str) or not isinstance(op["to"], str):
            raise SpecError("rename_variables: from/to должны быть строками")
    try:
        normalized_renames = normalize({"rename_variables": raw_renames})["rename_variables"]
    except ValueError as e:
        raise SpecError(f"rename_variables: {e}") from e
    all_from = {op["from"] for op in normalized_renames}
    seen_from: set[str] = set()
    seen_to: set[str] = set()
    for op in normalized_renames:
        old, new = op["from"], op["to"]
        if old == new:
            raise SpecError(f"rename_variables: {old!r} → {new!r}: имя не меняется")
        if old in seen_from:
            raise SpecError(f"rename_variables: {old!r} переименован дважды")
        if new in all_from:
            raise SpecError(f"rename_variables: {new!r} — источник другой операции "
                            f"в этой же спеке; цепочки переименований выполняйте "
                            f"отдельными правками")
        if new in seen_to:
            raise SpecError(f"rename_variables: два переименования дают одно имя {new!r}")
        seen_from.add(old)
        seen_to.add(new)


# --- сверка диффа со спецификацией ------------------------------------------

def _short(value: object, limit: int = 60) -> str:
    text = "—" if value is None else str(value).replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _edge_matches(wanted: tuple, actual: tuple) -> bool:
    """Совпадение ребра с элементом спецификации; None в спеке — любой порт/блок."""
    return all(w is None or w == a for w, a in zip(wanted, actual))


def compare_to_spec(diff: dict, spec: dict, old_model: dict | None = None) -> dict:
    """Доказательство «дифф совпал со спецификацией один в один».

    diff — результат process_diff.diff(old, new). Возвращает
    {ok, missing, unexpected, implied, checked}:
      missing    — спецификация требует, а в диффе этого нет;
      unexpected — в диффе есть, а спецификация этого не просила;
      implied    — изменения-следствия (связи удалённого блока), допустимы.

    old_model — модель «до» (process_diff.load). С ним rename_variables
    проверяется в обе стороны: каждое изменение свойства обязано быть
    ровно рефакторингом старого значения, а каждое значение, которое
    рефакторинг меняет, — обязано измениться. Без old_model проверяется
    только первая половина (по old внутри дельт).
    """
    from sherpa_xml.designer_refactor import rename_in_text

    spec = normalize(spec) if isinstance(spec, dict) else {}
    mapping = {op["from"]: op["to"] for op in spec["rename_variables"]}
    missing: list[str] = []
    unexpected: list[str] = []
    implied: list[str] = []

    # 1. Блоки
    want_add = {b["id"]: b for b in spec.get("add_blocks") or []}
    got_add = {b["id"]: b for b in diff.get("added_blocks") or []}
    for bid, want in want_add.items():
        got = got_add.get(bid)
        if got is None:
            missing.append(f"нет нового блока {bid[:8]} {want.get('internal')}")
            continue
        if want.get("internal") != got.get("internal"):
            unexpected.append(f"блок {bid[:8]}: InternalName {got.get('internal')!r} "
                              f"вместо {want.get('internal')!r}")
        if want.get("display") is not None and want["display"] != got.get("display"):
            unexpected.append(f"блок {bid[:8]}: DisplayName {_short(got.get('display'))!r} "
                              f"вместо ожидаемого {_short(want['display'])!r}")
        # свойства нового блока — часть доказательства: иначе «--expect» пропускает
        # файл, где клон получил не те значения (или свойства перетёрлись)
        got_props = got.get("props") or {}
        for name, value in (want.get("props") or {}).items():
            actual = got_props.get(name)
            if actual != value:
                unexpected.append(f"новый блок {bid[:8]}: свойство {name} = "
                                  f"{mask_value(name, actual)!r}, а в спеке "
                                  f"{mask_value(name, value)!r}")
    for bid in sorted(set(got_add) - set(want_add)):
        unexpected.append(f"лишний новый блок {bid[:8]} {got_add[bid].get('internal')}")

    want_del = set(spec.get("remove_blocks") or [])
    got_del = {b["id"] for b in diff.get("removed_blocks") or []}
    for bid in sorted(want_del - got_del):
        missing.append(f"блок {bid[:8]} не удалён")
    for bid in sorted(got_del - want_del):
        unexpected.append(f"удалён блок {bid[:8]}, которого нет в спецификации")

    # 2. Свойства: ровно заявленные (block, name), не больше;
    #    плюс изменения, являющиеся ровно рефакторингом rename_variables
    want_props = {(sp["block"], sp["name"]): sp.get("value")
                  for sp in spec.get("set_props") or []}
    seen_props: set[tuple[str, str]] = set()
    rename_changed: set[tuple[str, str]] = set()
    for block in diff.get("changed_blocks") or []:
        bid = block["id"]
        for d in block.get("deltas") or []:
            key = (bid, d["name"])
            if d["kind"] != "prop" or key not in want_props:
                if mapping and d["kind"] == "prop":
                    expected = rename_in_text(str(d.get("old") or ""), mapping)
                    if d.get("new") == expected and expected != d.get("old"):
                        rename_changed.add(key)
                        continue
                unexpected.append(f"блок {bid[:8]}: незаявленное изменение "
                                  f"{d['kind']} {d['name']} "
                                  f"({mask_value(d['name'], d.get('old'))} → "
                                  f"{mask_value(d['name'], d.get('new'))})")
                continue
            seen_props.add(key)
            # свойство заявлено set_props — его итог сверяется со значением
            # спеки; rename-обязательство (2b) для него снято явным значением
            rename_changed.add(key)
            want_value = want_props[key]
            if want_value is not None and want_value != d.get("new"):
                unexpected.append(f"блок {bid[:8]}: свойство {d['name']} = "
                                  f"{mask_value(d['name'], d.get('new'))!r}, а в спеке "
                                  f"{mask_value(d['name'], want_value)!r}")
    for key in sorted(set(want_props) - seen_props):
        missing.append(f"свойство {key[1]} блока {key[0][:8]} не изменилось")

    # 2b. rename_variables + old_model: всё, что рефакторинг обязан был
    #     изменить, — изменилось (обратная половина доказательства)
    if mapping and old_model is not None:
        for bid, blk in (old_model.get("blocks") or {}).items():
            for pname, pvalue in (blk.get("props") or {}).items():
                expected = rename_in_text(str(pvalue), mapping)
                if expected != pvalue and (bid, pname) not in rename_changed:
                    missing.append(f"переименование: свойство {pname} блока "
                                   f"{bid[:8]} содержит переменную, но не изменилось")

    # 3. Связи
    want_edges_add = {tuple(normalize({"add_edges": [list(x)]})["add_edges"][0])
                      for x in spec.get("add_edges") or []}
    got_edges_add = {tuple(e) for e in diff.get("added_edges") or []}
    for e in sorted(want_edges_add - got_edges_add):
        missing.append(f"нет новой связи {e[0][:8]} --{e[1]}--> {e[2][:8]}")
    for e in sorted(got_edges_add - want_edges_add):
        unexpected.append(f"лишняя связь {e[0][:8]} --{e[1]}--> {e[2][:8]}")
    want_edges_del = {tuple(normalize({"remove_edges": [list(x)]})["remove_edges"][0])
                      for x in spec.get("remove_edges") or []}
    got_edges_del = {tuple(e) for e in diff.get("removed_edges") or []}
    for e in sorted(got_edges_del):
        # связь удалённого блока исчезает как следствие — отдельно в спецификации не нужна
        if e[0] in want_del or e[2] in want_del:
            implied.append(f"связь удалённого блока {e[0][:8]} --{e[1]}--> {e[2][:8]}")
            continue
        if not any(_edge_matches(w, e) for w in want_edges_del):
            unexpected.append(f"удалена незаявленная связь {e[0][:8]} --{e[1]}--> {e[2][:8]}")
    for w in sorted(want_edges_del):
        if w[0] in want_del or w[2] in want_del:
            continue
        if not any(_edge_matches(w, e) for e in got_edges_del):
            src = (w[0] or "любой")[:8]
            dst = (w[2] or "любой")[:8]
            missing.append(f"связь {src} --{w[1] or 'любой'}--> {dst} не удалена")

    # 4. Всё, что спецификация не описывает вовсе.
    #    rename_variables описывает и состав переменных: объявление меняет имя,
    #    в diff это removed(старое)+added(новое) с теми же атрибутами.
    if mapping:
        ov, nv = diff.get("removed_variables") or [], diff.get("added_variables") or []
        removed_names = {v["name"] for v in ov}
        added_map = {v["name"]: v for v in nv}
        want_removed, want_added = set(mapping), set(mapping.values())
        for name in sorted(removed_names - want_removed):
            unexpected.append(f"удалена переменная {name!r}, которую спека не переименовывает")
        for name in sorted(want_removed - removed_names):
            missing.append(f"объявление переменной {name!r} не переименовано")
        for name in sorted(added_map):
            if name not in want_added:
                unexpected.append(f"появилась переменная {name!r} вне спецификации")
        for name in sorted(want_added - set(added_map)):
            missing.append(f"нет объявления переменной {name!r}")
        for old, new in mapping.items():
            if old in {v["name"] for v in ov} and new in added_map:
                src = next(v for v in ov if v["name"] == old)
                dst = added_map[new]
                for attr in ("type", "global"):
                    if src.get(attr) != dst.get(attr):
                        unexpected.append(f"при переименовании {old!r}→{new!r} "
                                          f"изменился атрибут {attr}")
                for attr in ("default", "value"):
                    if rename_in_text(str(src.get(attr) or ""), mapping) != str(dst.get(attr) or ""):
                        unexpected.append(f"при переименовании {old!r}→{new!r} "
                                          f"изменилось значение {attr} сверх рефакторинга")
    elif diff.get("added_variables") or diff.get("removed_variables"):
        unexpected.append("изменился состав переменных (спецификация этого не описывает)")
    if diff.get("changed_variables"):
        unexpected.append("изменились значения переменных (спецификация этого не описывает)")
    if diff.get("description_changed"):
        unexpected.append("изменился корневой Description")
    for key, title in (("added_sections", "добавлены секции"),
                       ("removed_sections", "удалены секции"),
                       ("changed_sections", "изменены секции")):
        if diff.get(key):
            unexpected.append(f"{title}: {', '.join(diff[key])}")

    return {
        "ok": not missing and not unexpected,
        "missing": missing,
        "unexpected": unexpected,
        "implied": implied,
        "checked": {
            "add_blocks": len(want_add), "remove_blocks": len(want_del),
            "add_edges": len(want_edges_add), "remove_edges": len(want_edges_del),
            "set_props": len(want_props), "rename_variables": len(mapping),
        },
    }


def render_check(result: dict) -> str:
    """Человеческий отчёт сверки «дифф ↔ спецификация»."""
    lines = []
    c = result["checked"]
    lines.append(f"сверка со спецификацией: блоков +{c['add_blocks']} / -{c['remove_blocks']}, "
                 f"связей +{c['add_edges']} / -{c['remove_edges']}, свойств {c['set_props']}"
                 + (f", переименований {c['rename_variables']}" if c.get("rename_variables") else ""))
    if result["ok"]:
        lines.append("  ИТОГ: дифф совпал со спецификацией один в один")
    else:
        lines.append("  ИТОГ: дифф НЕ совпал со спецификацией")
    for title, rows in (("чего не хватает", result["missing"]),
                        ("чего не просили", result["unexpected"])):
        if rows:
            lines.append(f"  {title}:")
            lines.extend(f"     - {r}" for r in rows)
    if result["implied"]:
        lines.append(f"  следствия (допустимы): {len(result['implied'])} "
                     f"связей удалённых блоков")
    return "\n".join(lines)
