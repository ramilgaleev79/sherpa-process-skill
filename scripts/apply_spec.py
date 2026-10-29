#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Применение спецификации правки к живому `.process` — последний ручной шаг закрыт.

До этого инструмента запись в файл была единственной неформализованной операцией:
под каждую задачу писались свои `patch.py`/`verify.py` (см. `.review/` в репозитории),
и «поменял только это» держалось на глазах. Здесь правка выполняется по той же
JSON-спецификации, по которой `change_impact.py` считает радиус, и сама себя
проверяет до перезаписи файла.

Что гарантируется
-----------------
1. **Новые блоки — клоны родных блоков этого же файла** (`clone_of`): `Code`,
   `Version`, `ConnectorsSettings` и порядок атрибутов сохраняются как есть;
   меняются только `ID`, `Left`/`Top`, `zIndex`, `DisplayName`/`Description`
   и явно заданные свойства.
2. **Байтовая аккуратность**: BOM, переводы строк и отступы — как в исходнике;
   текст файла не пересобирается целиком (правятся только затронутые фрагменты).
3. **Самопроверка до записи**: результат пишется во временный файл, `process_diff`
   сравнивает его с исходником, `edit_spec.compare_to_spec` доказывает, что дифф
   совпал со спецификацией один в один. Не совпал — файл не тронут.
4. **Новые дефекты не вносятся**: пост-валидация `validate_process_bytes` до и
   после; появился новый ERROR/FATAL — запись отменяется.
5. **Бэкап обязателен**: `--backup` без пути пишет `<FILE>.bak`; существующий
   бэкап с другим содержимым не перезаписывается.
6. **Переименование переменных (`rename_variables`) — с семантикой Designer**
   (IL-verified, `sherpa_xml/designer_refactor.py`): вне кавычек, вне зон
   `#stoprefactoring`, `${имя}` остаётся фигурной, `$env:` не трогается.
   Правка значений идёт прямо в файловом (дважды экранированном) тексте —
   нетронутые байты не переставляются; `set_props` на том же свойстве
   перекрывает рефакторинг; `from` обязан быть объявлен, `to` — не занят.
   Самопроверка (п.3) доказывает rename в обе стороны: каждое изменение —
   ровно рефакторинг, и всё, что рефакторинг меняет, — изменилось.

Использование:
    python scripts/apply_spec.py FILE.process --spec spec.json --backup
    python scripts/apply_spec.py FILE.process --spec spec.json --backup --dry-run
    python scripts/apply_spec.py FILE.process --spec spec.json --backup other.bak

Код возврата: 0 — записано (или --dry-run прошёл), 1 — проверка не прошла
(файл не тронут), 2 — ошибка спецификации/входа.
Только stdlib; схема спецификации — scripts/edit_spec.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_spec import (  # noqa: E402
    SpecError, compare_to_spec, escape_content, escape_tag_text, is_secret_name,
    mask_value, normalize, render_check, validate_spec,
)
from process_diff import diff as diff_models  # noqa: E402
from process_diff import load as load_model  # noqa: E402
from sherpa_xml import validator  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402
from sherpa_xml.designer_refactor import QUOTE_FILE, rename_in_text  # noqa: E402
from sherpa_xml.layout import SPACING_X, SPACING_Y  # noqa: E402

ITEM_RE = re.compile(r"[ \t]*<DesignerItem>.*?</DesignerItem>[ \t]*\r?\n", re.S)
CONN_RE = re.compile(r"[ \t]*<Connection>.*?</Connection>[ \t]*\r?\n", re.S)
MARGIN = 20
BOM = b"\xef\xbb\xbf"


class ApplyError(ValueError):
    """Правку нельзя выполнить: ошибка в спецификации или в самом файле."""


# --- чтение документа --------------------------------------------------------

class _Doc:
    """Текст `.process` + позиции блоков/связей (правки — по спанам, не пересборка).

    Кавычки внутри `Content` пишутся в двух видах: Designer ставит разделителем
    обычную `"`, а наш компилятор — `&quot;`. Оба файла валидны и разбираются
    одинаково, поэтому стиль определяется по файлу и применяется во всех правках.
    """

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.has_bom = raw.startswith(BOM)
        self.text = raw.decode("utf-8-sig")
        self.nl = "\r\n" if "\r\n" in self.text else "\n"
        self.items = [{"id": _tag(m.group(0), "ID"), "span": m.span(), "text": m.group(0)}
                      for m in ITEM_RE.finditer(self.text)]
        self.conns = [{"key": _conn_key(m.group(0)), "span": m.span(), "text": m.group(0)}
                      for m in CONN_RE.finditer(self.text)]
        if not self.items:
            raise ApplyError("в файле нет блоков DesignerItem — это не .process")
        self.indent = re.match(r"[ \t]*", self.items[0]["text"]).group(0)
        self.q = '"' if 'InternalName="' in self.text else "&quot;"
        self.blocks = {it["id"]: it for it in self.items}

    def rect(self, block_id: str) -> tuple[float, float, float, float]:
        text = self.blocks[block_id]["text"]
        return (_num(text, "Left"), _num(text, "Top"),
                _num(text, "Width"), _num(text, "Height"))

    def max_z(self) -> int:
        values = [_num(it["text"], "zIndex") for it in self.items]
        return int(max(values)) if values else 0

    def insert_point(self, section: str) -> int:
        """Позиция сразу после открывающего тега секции — вставка «в начало».

        Вставка в конец секции изменила бы `tail` последнего элемента (ET хранит
        отступ в tail), и нетронутый блок оказался бы «изменённым» в диффе; кроме
        того, позиция в конце может попасть внутрь удаляемого фрагмента.
        Вставка сразу после `<Section>` с префиксом `nl + indent` сохраняет
        отступы и tail всех существующих блоков.
        """
        marker = f"<{section}>"
        pos = self.text.find(marker)
        if pos < 0:
            raise ApplyError(f"в файле нет секции {section}")
        return pos + len(marker)


def _tag(block: str, name: str) -> str:
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
    if not m:
        raise ApplyError(f"в блоке нет тега {name}")
    return m.group(1)


def _num(block: str, name: str) -> float:
    try:
        return float(_tag(block, name))
    except (ApplyError, ValueError):
        return 0.0


def _conn_key(text: str) -> tuple:
    return (_tag(text, "SourceID"), _tag(text, "SourceConnectorName"),
            _tag(text, "SinkID"), _tag(text, "SinkConnectorName"))


def _set_tag(block: str, name: str, value: str) -> str:
    pattern = re.compile(r"(<%s>)(.*?)(</%s>)" % (name, name), re.S)
    if not pattern.search(block):
        raise ApplyError(f"в блоке нет тега {name}")
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), block, count=1)


def _attr(block: str, attr: str, q: str = '"') -> str | None:
    m = re.search(r"%s=%s(.*?)%s" % (re.escape(attr), re.escape(q), re.escape(q)), block, re.S)
    return m.group(1) if m else None


def _set_attr(block: str, attr: str, value: str, q: str) -> str:
    pattern = re.compile(r"(%s=%s).*?(%s)" % (re.escape(attr), re.escape(q), re.escape(q)), re.S)
    if not pattern.search(block):
        raise ApplyError(f"в блоке нет атрибута {attr}")
    return pattern.sub(lambda m: m.group(1) + value + m.group(2), block, count=1)


def _prop_bounds(block: str, name: str, q: str) -> tuple[int, int]:
    m = re.search(r"Name=%s%s%s" % (re.escape(q), re.escape(name), re.escape(q)), block)
    if not m:
        raise ApplyError(f"у блока-прототипа нет свойства {name!r}: "
                         f"свойство нельзя «добавить», его задаёт шаблон Designer")
    # внутри Content символы < > экранированы: конец тега — `/&gt;`, а не `/>`
    for closing in ("/&gt;", "/>"):
        end = block.find(closing, m.end())
        if end >= 0:
            return m.start(), end + len(closing)
    raise ApplyError(f"свойство {name!r} не закрыто")


def _set_prop(block: str, name: str, encoded: str | None, q: str) -> str:
    """Записать значение свойства. encoded=None — вернуть значение по умолчанию."""
    start, end = _prop_bounds(block, name, q)
    segment = block[start:end]
    vm = re.search(r"(?<![A-Za-z])Value=%s" % re.escape(q), segment)
    if not vm:
        raise ApplyError(f"у свойства {name!r} нет атрибута Value")
    value_start = start + vm.end()
    value_end = block.index(q, value_start)
    if encoded is not None:
        return block[:value_start] + encoded + block[value_end:]
    dm = re.search(r"(?<= )DefaultValue=%s" % re.escape(q), segment)
    if not dm:
        raise ApplyError(f"у свойства {name!r} нет DefaultValue — снять переопределение нельзя")
    default_start = start + dm.end()
    default_end = block.index(q, default_start)
    return block[:value_start] + block[default_start:default_end] + block[value_end:]


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + MARGIN < bx or bx + bw + MARGIN < ax
                or ay + ah + MARGIN < by or by + bh + MARGIN < ay)


def _place(doc: _Doc, spec_block: dict, taken: list[tuple]) -> tuple[int, int]:
    """Детерминированная позиция нового блока (шаги раскладки — из layout.py)."""
    place = spec_block.get("place") or {}
    if place.get("left") is not None and place.get("top") is not None:
        return int(place["left"]), int(place["top"])
    anchor = place.get("anchor")
    if anchor:
        ax, ay, _aw, ah = doc.rect(anchor)
        left, top = ax, ay + ah + MARGIN
    elif place.get("left") is not None:
        left, top = int(place["left"]), min((r[1] for r in taken), default=0.0)
    elif place.get("top") is not None:
        left, top = max((r[0] + r[2] for r in taken), default=0.0) + SPACING_X, int(place["top"])
    else:
        left = max((r[0] + r[2] for r in taken), default=0.0) + SPACING_X
        top = min((r[1] for r in taken), default=0.0)
    width = _num(doc.blocks[spec_block["clone_of"]]["text"], "Width") if spec_block.get("clone_of") else 200
    height = _num(doc.blocks[spec_block["clone_of"]]["text"], "Height") if spec_block.get("clone_of") else 75
    while any(_overlaps((left, top, width, height), r) for r in taken):
        top += SPACING_Y
    return int(left), int(top)


# --- сборка новой версии текста ---------------------------------------------

def build_text(doc: _Doc, spec: dict) -> tuple[str, list[dict]]:
    """Возвращает новый текст файла и журнал операций (без записи на диск)."""
    spec = normalize(spec)
    edits: list[tuple[int, int, str]] = []      # (start, end, replacement)
    journal: list[dict] = []
    removed = set(spec["remove_blocks"])
    taken = [doc.rect(b) for b in doc.blocks]
    zindex = doc.max_z()

    # 0. переименование переменных «как Designer»: значения свойств всех блоков
    #    + объявления в Properties. База для последующих шагов — переименованный
    #    текст (set_props на той же собственности перекрывает рефакторинг).
    mapping = {op["from"]: op["to"] for op in spec["rename_variables"]}
    base_texts: dict[str, str] = {}
    if mapping:
        stats: dict = {"blocks": set(), "replace": 0, "in_quotes": 0,
                       "directive_zone": 0, "scope_qualified": 0}
        for bid, item in doc.blocks.items():
            if bid in removed:
                continue
            renamed = _rename_block_text(item["text"], bid, mapping, doc.q, stats)
            if renamed != item["text"]:
                base_texts[bid] = renamed
                edits.append((item["span"][0], item["span"][1], renamed))
        section, renamed_decls = _rename_properties_section(doc.text, mapping, doc.q)
        props_m = re.search(r"<Properties\b[^>]*>.*?</Properties>", doc.text, re.S)
        edits.append((props_m.start(), props_m.end(), section))
        journal.append({
            "op": "rename_variables",
            "ops": spec["rename_variables"],
            "blocks": len(stats["blocks"]),
            "replace": stats["replace"], "in_quotes": stats["in_quotes"],
            "directive_zone": stats["directive_zone"],
            "scope_qualified": stats["scope_qualified"],
            "declarations": renamed_decls,
        })

    def _base(bid: str) -> str:
        return base_texts.get(bid) or doc.blocks[bid]["text"]

    # 1. удаление блоков вместе с их связями
    for bid in spec["remove_blocks"]:
        item = doc.blocks[bid]
        edits.append((item["span"][0], item["span"][1], ""))
        journal.append({"op": "remove_block", "id": bid,
                        "internal": _attr(item["text"], "InternalName", doc.q)})
    for conn in doc.conns:
        if conn["key"][0] in removed or conn["key"][2] in removed:
            edits.append((conn["span"][0], conn["span"][1], ""))
            journal.append({"op": "remove_edge", "edge": list(conn["key"]),
                            "reason": "связь удалённого блока"})

    # 2. удаление заявленных связей
    for edge in spec["remove_edges"]:
        matched = [c for c in doc.conns if _edge_matches(edge, c["key"])]
        if not matched:
            raise ApplyError(f"remove_edges: связи {list(edge)} в файле нет")
        for conn in matched:
            if conn["key"][0] in removed or conn["key"][2] in removed:
                continue          # уже удалена вместе с блоком
            edits.append((conn["span"][0], conn["span"][1], ""))
            journal.append({"op": "remove_edge", "edge": list(conn["key"])})

    # 3. изменение свойств существующих блоков
    for sp in spec["set_props"]:
        item = doc.blocks[sp["block"]]
        encoded = None if sp.get("value") is None else escape_content(sp["value"])
        try:
            new_text = _set_prop(_base(sp["block"]), sp["name"], encoded, doc.q)
        except ApplyError as exc:
            raise ApplyError(f"set_props {sp['block'][:8]}.{sp['name']}: {exc}") from exc
        # если блок уже переименован (шаг 0), правка заменяет ту же — иначе правки
        # пересекутся, а пересечение запрещено
        span = item["span"]
        for idx, (s, e, _t) in enumerate(edits):
            if s == span[0] and e == span[1]:
                edits[idx] = (s, e, new_text)
                break
        else:
            edits.append((span[0], span[1], new_text))
        # в журнал (и в --json) значение секретного свойства не пишем
        value = sp.get("value")
        if value is not None and is_secret_name(sp["name"]):
            value = "«секрет скрыт»"
        journal.append({"op": "set_prop", "id": sp["block"], "name": sp["name"],
                        "value": value})

    # 4. новые блоки — клоны родных блоков этого же файла
    new_items: list[str] = []
    for nb in spec["add_blocks"]:
        clone_id = nb.get("clone_of") or _find_same_internal(doc, nb["internal"])
        if clone_id is None:
            raise ApplyError(f"add_blocks {nb['id'][:8]} {nb['internal']}: нет родного блока "
                             f"для клонирования — укажите clone_of (сборка с нуля — через IR)")
        clone = _base(clone_id).lstrip(" \t")
        clone_internal = _attr(doc.blocks[clone_id]["text"], "InternalName", doc.q)
        if nb.get("internal") != clone_internal:
            raise ApplyError(f"add_blocks {nb['id'][:8]}: clone_of указывает на "
                             f"{clone_internal}, а заявлен {nb['internal']} — "
                             f"клон изменил бы смысл блока")
        block = _set_tag(clone, "ID", nb["id"])
        # выключенный прототип не должен передавать IsEnabled=false новому блоку:
        # добавляемый блок — часть новой ветки, а не «выключенная копия»
        if "<IsEnabled>" in block:
            block = _set_tag(block, "IsEnabled", "true")
        left, top = _place(doc, {**nb, "clone_of": clone_id}, taken)
        taken.append((left, top, _num(clone, "Width"), _num(clone, "Height")))
        zindex += 1
        block = _set_tag(block, "Left", str(left))
        block = _set_tag(block, "Top", str(top))
        block = _set_tag(block, "zIndex", str(zindex))
        if nb.get("description") is not None:
            block = _set_tag(block, "Description", escape_tag_text(nb["description"]))
        if nb.get("display") is not None:
            block = _set_attr(block, "DisplayName", escape_content(nb["display"]), doc.q)
        for name, value in (nb.get("props") or {}).items():
            try:
                block = _set_prop(block, name, escape_content(value), doc.q)
            except ApplyError as exc:
                raise ApplyError(f"add_blocks {nb['id'][:8]}.{name}: {exc}") from exc
        new_items.append(block)
        journal.append({"op": "add_block", "id": nb["id"], "internal": nb["internal"],
                        "clone_of": clone_id, "left": left, "top": top,
                        "props": sorted((nb.get("props") or {})), "display": nb.get("display")})

    # 5. новые связи — по образцу существующей (порядок полей как в файле)
    template = doc.conns[0]["text"].lstrip(" \t") if doc.conns else _connection_template()
    new_conns: list[str] = []
    for edge in spec["add_edges"]:
        src, port, dst, sink = edge
        conn = _set_tag(template, "SourceID", src)
        conn = _set_tag(conn, "SinkID", dst)
        conn = _set_tag(conn, "SourceConnectorName", port)
        conn = _set_tag(conn, "SinkConnectorName", sink)
        zindex += 1
        conn = _set_tag(conn, "zIndex", str(zindex))
        new_conns.append(conn)
        journal.append({"op": "add_edge", "edge": [src, port, dst, sink]})

    # 6. правки — одной сборкой: вставки в начало секций, остальное по спанам
    text = doc.text
    sep = doc.nl + doc.indent
    if new_items:
        at = doc.insert_point("DesignerItems")
        edits.append((at, at, sep + sep.join(new_items)))
    if new_conns:
        at = doc.insert_point("Connections")
        edits.append((at, at, sep + sep.join(new_conns)))
    return _apply_edits(doc.text, edits), journal


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Одна сборка текста: правки не пересекаются, порядок — по позиции.

    Применять правки по одной в обратном порядке нельзя: вставка сдвигает
    смещения, и последующее удаление режет не тот участок. Поэтому текст
    собирается за один проход, а пересечение правок — ошибка, а не «как-нибудь».
    """
    out: list[str] = []
    pos = 0
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1])):
        if start < pos:
            raise ApplyError("правки пересекаются — спецификация противоречива")
        out.append(text[pos:start])
        out.append(replacement)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _find_same_internal(doc: _Doc, internal: str) -> str | None:
    for item in doc.items:
        if _attr(item["text"], "InternalName", doc.q) == internal:
            return item["id"]
    return None


def _edge_matches(wanted: tuple, actual: tuple) -> bool:
    return all(w is None or w == a for w, a in zip(wanted, actual))


def _connection_template() -> str:
    return ("<Connection>\r\n"
            "      <SourceID></SourceID>\r\n"
            "      <SinkID></SinkID>\r\n"
            "      <SourceConnectorName>Next</SourceConnectorName>\r\n"
            "      <SinkConnectorName>Input</SinkConnectorName>\r\n"
            "      <SourceArrowSymbol>None</SourceArrowSymbol>\r\n"
            "      <SinkArrowSymbol>Arrow</SinkArrowSymbol>\r\n"
            "      <zIndex>0</zIndex>\r\n"
            "    </Connection>")


# --- переименование переменных (семантика Designer, IL-verified) ------------

def _rename_block_text(block: str, bid: str, mapping: dict, q: str, stats: dict) -> str:
    """Rename `Value="…"` всех свойств блока — прямо в тексте файла.

    Работаем в QUOTE_FILE-пространстве (PS-кавычка = `&amp;quot;`), без
    unescape→escape: нетронутые части байта-в-байта сохраняются. DefaultValue
    не трогаем — это шаблонное значение блока, а не пользовательское выражение.
    """
    quote_mark = QUOTE_FILE if q == '"' else QUOTE_FILE.replace("&", "&amp;")
    pat = re.compile(r"(Value=%s)(.*?)(%s)" % (re.escape(q), re.escape(q)), re.S)

    def sub(m: re.Match) -> str:
        findings: list[dict] = []
        new = rename_in_text(m.group(2), mapping, quote_mark=quote_mark,
                             findings=findings)
        if findings:
            stats["blocks"].add(bid)
            for f in findings:
                stats[f["kind"]] = stats.get(f["kind"], 0) + 1
        return m.group(1) + new + m.group(3)

    return pat.sub(sub, block)


def _rename_properties_section(text: str, mapping: dict, q: str) -> tuple[str, int]:
    """Переименовать объявления DiagramProperty в секции Properties.

    Возвращает (новый текст секции, число переименованных объявлений).
    Self-closing `<Properties />` — переменных нет, это ошибка вызова.
    """
    m = re.search(r"<Properties\b[^>]*>.*?</Properties>", text, re.S)
    if not m:
        raise ApplyError("в файле нет секции Properties — переименовывать объявления некуда")
    section = m.group(0)
    renamed = 0
    for old, new in mapping.items():
        pat = re.compile(r"(<DiagramProperty\b[^>]*?Name=%s)%s(%s)"
                         % (re.escape(q), re.escape(old), re.escape(q)))
        section, n = pat.subn(lambda mm: mm.group(1) + new + mm.group(2), section)
        renamed += n
    return section, renamed


def _validate_rename_targets(spec: dict, model: dict) -> None:
    """`from` обязан быть объявленной переменной файла; `to` — не существовать."""
    renames = normalize(spec)["rename_variables"]
    if not renames:
        return
    declared = set(model.get("variables") or {})
    for op in renames:
        if op["from"] not in declared:
            raise ApplyError(f"rename_variables: переменная {op['from']!r} "
                             f"не объявлена в этом файле")
        if op["to"] in declared:
            raise ApplyError(f"rename_variables: имя {op['to']!r} уже занято "
                             f"другой переменной файла")


# --- проверка и запись -------------------------------------------------------

def _new_blocking_issues(before: dict, after: dict) -> list[dict]:
    def blocking(report):
        return [i for i in report["issues"] if i["severity"] in ("FATAL", "ERROR")]

    def identity(issue):
        return (issue["code"], issue["location"], issue["message"])

    seen = {identity(i) for i in blocking(before)}
    return [i for i in blocking(after) if identity(i) not in seen]


def apply(path: Path, spec: dict, *, backup: Path | None, dry_run: bool,
          force_backup: bool, allow_new_issues: bool) -> dict:
    doc = _Doc(path)
    model = load_model(path)
    validate_spec(spec, {bid: {"id": bid, "internal": _attr(it["text"], "InternalName", doc.q)}
                         for bid, it in doc.blocks.items()})
    _validate_rename_targets(spec, model)
    # автосейв Designer: скрытый ~<имя> рядом с файлом может нести устаревшее
    # состояние; после правки он не синхронизируется сам (заметка в отчёт)
    autosave = path.with_name("~" + path.name)
    autosave_note = str(autosave) if autosave.exists() else None
    text, journal = build_text(doc, spec)
    payload = (BOM if doc.has_bom else b"") + text.encode("utf-8")

    # 1) пост-валидация: новых блокирующих дефектов быть не должно
    before = validator.validate_process_bytes(path.read_bytes())
    after = validator.validate_process_bytes(payload)
    fresh = _new_blocking_issues(before, after)
    if fresh and not allow_new_issues:
        lines = [f"[{i['severity']}] {i['code']} {i['location']}: {i['message']}" for i in fresh]
        raise ApplyError("правка вносит новые дефекты (--allow-new-issues, чтобы принять):\n  "
                         + "\n  ".join(lines))

    # 2) самопроверка: дифф ровно по спецификации (до записи файла)
    tmp = path.with_name(path.name + ".apply.tmp")
    tmp.write_bytes(payload)
    try:
        check = compare_to_spec(diff_models(model, load_model(tmp)), spec,
                                old_model=model)
    finally:
        tmp.unlink(missing_ok=True)
    if not check["ok"]:
        raise ApplyError("дифф не совпал со спецификацией — файл не тронут:\n"
                         + render_check(check))

    result = {
        "file": str(path),
        "bytes_before": path.stat().st_size,
        "bytes_after": len(payload),
        "operations": journal,
        "check": check,
        "new_issues": fresh,
        "autosave_leftover": autosave_note,
        "written": False,
        "backup": None,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    # 3) бэкап обязателен: без него правка живого сценария запрещена
    if backup is None:
        raise ApplyError("правка без бэкапа запрещена: добавьте --backup [PATH]")
    original = path.read_bytes()
    if backup.exists() and backup.read_bytes() != original and not force_backup:
        raise ApplyError(f"бэкап {backup} уже существует и отличается от текущего файла; "
                         f"укажите другой путь или --force-backup")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, backup)

    # 4) запись: новый текст — во временный файл рядом, затем атомарная замена
    staged = path.with_name(path.name + ".new")
    try:
        staged.write_bytes(payload)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    result["written"] = True
    result["backup"] = str(backup)
    return result


def report(res: dict) -> None:
    print(f"{res['file']}: {res['bytes_before']} → {res['bytes_after']} байт")
    for op in res["operations"]:
        if op["op"] == "add_block":
            print(f"  + блок {op['id'][:8]} {op['internal']:20s} "
                  f"(клон {op['clone_of'][:8]}, {op['left']},{op['top']})")
        elif op["op"] == "remove_block":
            print(f"  - блок {op['id'][:8]} {op['internal']}")
        elif op["op"] == "add_edge":
            e = op["edge"]
            print(f"  + связь {e[0][:8]} --{e[1]}--> {e[2][:8]}")
        elif op["op"] == "remove_edge":
            e = op["edge"]
            print(f"  - связь {e[0][:8]} --{e[1]}--> {e[2][:8]}"
                  + (f"  ({op['reason']})" if op.get("reason") else ""))
        elif op["op"] == "set_prop":
            print(f"  ~ блок {op['id'][:8]} свойство {op['name']} = "
                  + ("<по умолчанию>" if op["value"] is None
                     else mask_value(op["name"], op["value"])))
        elif op["op"] == "rename_variables":
            pairs = ", ".join(f"{o['from']}→{o['to']}" for o in op["ops"])
            print(f"  ≡ переименовано: {pairs}")
            print(f"     блоков задето {op['blocks']}, замен {op['replace']}; "
                  f"Designer НЕ тронет: в кавычках {op['in_quotes']}, "
                  f"в зонах #stoprefactoring {op['directive_zone']}, "
                  f"квалификаторов $x: {op['scope_qualified']}; "
                  f"объявлений {op['declarations']}")
    if res.get("autosave_leftover"):
        print(f"  ! рядом лежит автосейв Designer {res['autosave_leftover']} — "
              f"он не синхронизируется с правкой; откройте-закройте файл "
              f"в Designer или удалите его вручную")
    print(render_check(res["check"]))
    if res["dry_run"]:
        print("--dry-run: файл не изменён")
    elif res["written"]:
        print(f"записано: {res['file']}; бэкап: {res['backup']}")


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Записать спецификацию правки в .process")
    ap.add_argument("file", type=Path)
    ap.add_argument("--spec", type=Path, required=True, help="JSON со спецификацией правки")
    ap.add_argument("--backup", nargs="?", const="", default=None,
                    help="бэкап (по умолчанию <FILE>.bak); флаг обязателен для записи")
    ap.add_argument("--dry-run", action="store_true", help="проверить и показать, не писать")
    ap.add_argument("--force-backup", action="store_true", help="перезаписать существующий бэкап")
    ap.add_argument("--allow-new-issues", action="store_true",
                    help="разрешить новые ERROR пост-валидации (не рекомендуется)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.file.exists():
        print(f"нет файла: {args.file}", file=sys.stderr)
        return 2
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: спецификация не читается: {exc}", file=sys.stderr)
        return 2
    backup = None
    if args.backup is not None:
        backup = Path(args.backup) if args.backup else args.file.with_name(args.file.name + ".bak")
    try:
        res = apply(args.file, spec, backup=backup, dry_run=args.dry_run,
                    force_backup=args.force_backup,
                    allow_new_issues=args.allow_new_issues)
    except (SpecError, ApplyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1 if isinstance(exc, ApplyError) else 2
    except (OSError, ET.ParseError, UnicodeDecodeError) as exc:
        print(f"FAIL: файл не читается: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "check"}
                         | {"check": res["check"]}, ensure_ascii=False, indent=1))
    else:
        report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
