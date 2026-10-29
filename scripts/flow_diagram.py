#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Блок-схема `.process` в HTML с размеченными багами: красное — где, зелёное — как чинить.

Рендерер НАВИГАЦИОННЫЙ: он не ищет дефекты (их ищет ревью — `lint_process.py`,
`structure_audit.py`, чтение смысла), а рисует уже найденные. Контракт входа
и пайплайн — `references/flow-diagram.md`; ключевые свойства:

  * детерминизм — один `.process` + один findings.json → байт-в-байт один HTML
    (нет времени, случайных id, абсолютных путей; `--check` сверяет два рендера);
  * нумерация багов каноническая: сортировка (P0<P1<P2, верхний-левый блок,
    id блока, title) — одни и те же баги получают одни и те же номера при любом
    порядке записей во входном JSON;
  * все баги — одним файлом и одной картинкой: метки строятся по всему списку;
  * валидация до отрисовки: ссылка на блок обязана разрешаться ровно в один
    блок (FD-REF-001/FD-REF-002), поля схемы — строгие (FD-SCHEMA-*).

Использование:
    python scripts/flow_diagram.py FILE.process [--findings bugs.json]
                                               [--out OUT.html] [--title TITLE] [--check]

Код возврата: 0 — схема готова; 1 — findings не прошёл валидацию (или
детерминизм-чек); 2 — `.process` не читается. Только stdlib, без JS в выходе.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sherpa_xml.console import utf8_stdio  # noqa: E402
from structure_audit import load as load_graph  # noqa: E402

SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
SEVERITY_COLOR = {"P0": "#d92d20", "P1": "#dc6803", "P2": "#667085"}
BUG_FILL, BUG_STROKE = "#fdecea", "#d92d20"
FIX_FILL, FIX_STROKE = "#e9f9ef", "#12b76a"
GHOST_FILL, GHOST_STROKE = "#f2fbf5", "#12b76a"
NODE_FILL, NODE_STROKE = "#ffffff", "#b8c0cc"
EDGE_COLOR, LABEL_COLOR = "#98a2b3", "#475467"
PAGE_BG, TEXT_COLOR = "#f7f8fa", "#1d2939"
TITLE_MAX = 26          # обрезка DisplayName в узле
CODE_LINE_MAX, CODE_LINES_MAX = 34, 3


class FindingError(Exception):
    """Нарушение контракта findings.json: рендер не начинается."""


# ---------------------------------------------------------------- парсинг

def _coord(item: ET.Element, tag: str) -> int:
    text = item.findtext(tag) or "0"
    return int(round(float(text)))


def load_nodes(path: Path) -> tuple[dict[str, dict], list[tuple[str, str, str, str]]]:
    """Граф — из structure_audit.load (одна модель с аудитом/линтом),
    геометрия — прямо из DesignerItem: схема повторяет холст Designer."""
    blocks, edges = load_graph(path)
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    items = root.find("DesignerItems")
    for it in items if items is not None else []:
        bid = it.findtext("ID")
        if bid not in blocks:
            continue
        blocks[bid]["x"] = _coord(it, "Left")
        blocks[bid]["y"] = _coord(it, "Top")
        blocks[bid]["w"] = max(120, _coord(it, "Width"))
        blocks[bid]["h"] = max(48, _coord(it, "Height"))
    return blocks, edges


# ------------------------------------------------------- валидация findings

def _require(cond: bool, code: str, msg: str) -> None:
    if not cond:
        raise FindingError(f"{code}: {msg}")


def resolve_ref(ref: str, blocks: dict[str, dict]) -> str:
    """Полный UUID или префикс (≥8) — ровно в один блок."""
    _require(isinstance(ref, str) and len(ref) >= 8, "FD-SCHEMA-003",
             f"ссылка на блок '{ref}': ждём UUID или префикс ≥ 8 символов")
    if ref in blocks:
        return ref
    matches = sorted(b for b in blocks if b.startswith(ref))
    _require(bool(matches), "FD-REF-001",
             f"блок '{ref}' не найден (нет ID с таким префиксом)")
    _require(len(matches) == 1, "FD-REF-002",
             f"префикс '{ref}' неоднозначен: " + ", ".join(matches))
    return matches[0]


def _refs_list(value, field: str, blocks: dict[str, dict], where: str) -> list[str]:
    _require(isinstance(value, list) and value and all(isinstance(v, str) for v in value),
             "FD-SCHEMA-003", f"{where}.{field}: ждём непустой массив строк")
    return [resolve_ref(r, blocks) for r in value]


def validate_findings(data, blocks: dict[str, dict]) -> list[dict]:
    """Схема → список находок с разрешёнными id; канонический порядок и номера."""
    _require(isinstance(data, dict), "FD-SCHEMA-001", "корень findings.json — объект")
    raw = data.get("findings")
    _require(isinstance(raw, list) and raw, "FD-SCHEMA-002",
             "findings — непустой массив (пустой список багов = не запускайте рендер)")
    findings: list[dict] = []
    for i, f in enumerate(raw):
        w = f"findings[{i}]"
        _require(isinstance(f, dict), "FD-SCHEMA-003", f"{w}: ждём объект")
        for field in ("severity", "title", "where", "what"):
            _require(field in f, "FD-SCHEMA-003", f"{w}: нет обязательного поля '{field}'")
        severity = f["severity"]
        _require(severity in SEVERITY_RANK, "FD-SCHEMA-004",
                 f"{w}.severity: {severity!r} — ждём P0 | P1 | P2")
        title = f["title"]
        _require(isinstance(title, str) and 0 < len(title) <= 120, "FD-SCHEMA-003",
                 f"{w}.title: строка 1..120 символов")
        _require(isinstance(f["what"], str) and f["what"].strip(), "FD-SCHEMA-003",
                 f"{w}.what: непустая строка")
        # форма fix проверяется до разрешения ссылок: ошибки схемы важнее, чем
        # то, что по ссылке не нашлось блока
        fix_raw = None
        if "fix" in f and f["fix"] is not None:
            fx, fw = f["fix"], f"{w}.fix"
            _require(isinstance(fx, dict), "FD-SCHEMA-005", f"{fw}: ждём объект")
            _require(isinstance(fx.get("text"), str) and fx["text"].strip(),
                     "FD-SCHEMA-005", f"{fw}.text: обязательная непустая строка")
            fix_raw = fx
        item = {
            "severity": severity,
            "title": title.strip(),
            "where": _refs_list(f["where"], "where", blocks, w),
            "what": f["what"].strip(),
            "fix": None,
        }
        if fix_raw is not None:
            fw = f"{w}.fix"
            fx = fix_raw
            item["fix"] = {"text": fx["text"].strip(),
                           "where": _refs_list(fx["where"], "where", blocks, fw)
                           if isinstance(fx.get("where"), list) and fx["where"] else [],
                           "insert_after":
                               _refs_list(fx["insert_after"], "insert_after", blocks, fw)
                               if isinstance(fx.get("insert_after"), list)
                               and fx["insert_after"] else [],
                           "insert_title": (fx.get("insert_title") or "Новый блок").strip(),
                           "insert_code": (fx.get("insert_code") or "").strip()}
        findings.append(item)

    def order_key(f: dict) -> tuple:
        anchors = sorted((blocks[b]["y"], blocks[b]["x"], b) for b in f["where"])
        return (SEVERITY_RANK[f["severity"]], anchors[0], f["title"])

    findings.sort(key=order_key)
    for n, f in enumerate(findings, start=1):
        f["num"] = n
    return findings


# ----------------------------------------------------------------- рендер

def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _wrap_code(code: str) -> list[str]:
    lines, current = [], ""
    for word in code.split():
        candidate = word if not current else current + " " + word
        if len(candidate) > CODE_LINE_MAX and current:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) == CODE_LINES_MAX:
            break
    if current and len(lines) < CODE_LINES_MAX:
        lines.append(current)
    if len(lines) == CODE_LINES_MAX and (current != lines[-1]):
        lines[-1] = _truncate(lines[-1], CODE_LINE_MAX)
    return lines or [""]


def _edge_path(x1: float, y1: float, x2: float, y2: float) -> str:
    """Вперёд/вбок — прямая; назад (вверх по холсту) — кривая вправо,
    чтобы циклы не сливались с прямым потоком."""
    if y2 >= y1:
        return f"M {x1:.0f} {y1:.0f} L {x2:.0f} {y2:.0f}"
    cx = max(x1, x2) + 90
    cy = (y1 + y2) / 2
    return f"M {x1:.0f} {y1:.0f} Q {cx:.0f} {cy:.0f} {x2:.0f} {y2:.0f}"


def _port_pos(x1: float, y1: float, x2: float, y2: float, t: float = 0.45) -> tuple[float, float]:
    """Точка на прямой/квадратичной кривой (для подписи порта)."""
    if y2 >= y1:
        return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
    cx, cy = max(x1, x2) + 90, (y1 + y2) / 2
    mt = 1 - t
    return (mt * mt * x1 + 2 * mt * t * cx + t * t * x2,
            mt * mt * y1 + 2 * mt * t * cy + t * t * y2)


def render(path: Path, findings: list[dict] | None, title: str | None) -> str:
    blocks, edges = load_nodes(path)
    ids = sorted(blocks)
    bug_at: dict[str, list[int]] = {}
    fix_at: dict[str, list[int]] = {}
    inserts: list[tuple[dict, str]] = []          # (finding, anchor_id)
    if findings:
        for f in findings:
            for b in f["where"]:
                bug_at.setdefault(b, []).append(f["num"])
            if f["fix"]:
                for b in f["fix"]["where"]:
                    fix_at.setdefault(b, []).append(f["num"])
                for b in f["fix"]["insert_after"]:
                    inserts.append((f, b))

    svg_w = max((blocks[b]["x"] + blocks[b]["w"]) for b in ids) + 120
    svg_h = max((blocks[b]["y"] + blocks[b]["h"]) for b in ids) + 80

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" '
        f'role="img" aria-label="Блок-схема { _esc(path.name) }">')
    parts.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{EDGE_COLOR}"/></marker></defs>')

    # связи (под узлами): линия центр-центр, узел закрывает «вход» стрелки
    for s, sport, t, tport in edges:
        if s not in blocks or t not in blocks:
            continue
        sb, tb = blocks[s], blocks[t]
        x1, y1 = sb["x"] + sb["w"] / 2, sb["y"] + sb["h"] / 2
        x2, y2 = tb["x"] + tb["w"] / 2, tb["y"] + tb["h"] / 2
        parts.append(f'<path d="{_edge_path(x1, y1, x2, y2)}" fill="none" '
                     f'stroke="{EDGE_COLOR}" stroke-width="1.5" marker-end="url(#arrow)"/>')
        if sport:
            px, py = _port_pos(x1, y1, x2, y2)
            parts.append(f'<text x="{px + 5:.0f}" y="{py - 4:.0f}" font-size="10" '
                         f'fill="{LABEL_COLOR}">{_esc(sport)}</text>')

    # зелёные ghost-узлы «новый блок»: справа от якоря, без наездов (детерминированный сдвиг)
    occupied = [(blocks[b]["x"], blocks[b]["y"], blocks[b]["w"], blocks[b]["h"]) for b in ids]
    for f, anchor in inserts:
        a = blocks[anchor]
        gx, gy, gw, gh = a["x"] + a["w"] + 50, a["y"], 210, 76
        while any(gx < ox + ow and gx + gw > ox and gy < oy + oh and gy + gh > oy
                  for ox, oy, ow, oh in occupied):
            gx += 140
        occupied.append((gx, gy, gw, gh))
        parts.append(f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="10" '
                     f'fill="{GHOST_FILL}" stroke="{GHOST_STROKE}" stroke-width="2" '
                     'stroke-dasharray="7 4"/>')
        parts.append(f'<text x="{gx + gw / 2:.0f}" y="{gy + 18}" text-anchor="middle" '
                     f'font-size="10" font-weight="700" fill="{GHOST_STROKE}">НОВЫЙ БЛОК</text>')
        parts.append(f'<text x="{gx + gw / 2:.0f}" y="{gy + 35}" text-anchor="middle" '
                     f'font-size="10.5" fill="{TEXT_COLOR}">'
                     f'{_esc(_truncate(f["fix"]["insert_title"], TITLE_MAX))}</text>')
        for li, line in enumerate(_wrap_code(f["fix"]["insert_code"])):
            parts.append(f'<text x="{gx + gw / 2:.0f}" y="{gy + 52 + li * 13}" '
                         f'text-anchor="middle" font-size="9.5" font-family="Consolas,monospace" '
                         f'fill="{LABEL_COLOR}">{_esc(line)}</text>')
        parts.append(f'<path d="M {a["x"] + a["w"]} {a["y"] + a["h"] / 2} '
                     f'L {gx} {gy + gh / 2}" stroke="{GHOST_STROKE}" stroke-width="1.5" '
                     'stroke-dasharray="4 4" fill="none"/>')
        # зелёный номер — на ghost-узле
        parts.append(_badge(gx + 12, gy + 1, f["num"], FIX_STROKE))

    # узлы
    for bid in ids:
        b = blocks[bid]
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        is_bug, is_fix = bid in bug_at, bid in fix_at
        fill = BUG_FILL if is_bug else (FIX_FILL if is_fix else NODE_FILL)
        stroke = BUG_STROKE if is_bug else (FIX_STROKE if is_fix else NODE_STROKE)
        sw = "2.5" if (is_bug or is_fix) else "1.2"
        opacity = ' opacity="0.45"' if not b["enabled"] else ""
        display = _truncate(b["display"] or b["internal"], TITLE_MAX)
        if not b["enabled"]:
            display += " (OFF)"
        parts.append(f'<g{opacity}><title>{_esc(bid)} — {_esc(b["internal"])} '
                     f'{_esc(b["display"])}</title>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        parts.append(f'<text x="{x + w / 2:.0f}" y="{y + h / 2 - 3}" text-anchor="middle" '
                     f'font-size="10.5" fill="{TEXT_COLOR}">{_esc(display)}</text>')
        parts.append(f'<text x="{x + w / 2:.0f}" y="{y + h / 2 + 12}" text-anchor="middle" '
                     f'font-size="9.5" fill="{LABEL_COLOR}">{_esc(bid[:8])}</text>')
        parts.append("</g>")
        for i, num in enumerate(sorted(bug_at.get(bid, []))):
            parts.append(_badge(x + 12 + i * 17, y + 1, num, BUG_STROKE))
        for i, num in enumerate(sorted(fix_at.get(bid, []))):
            parts.append(_badge(x + w - 12 - i * 17, y + 1, num, FIX_STROKE))
    parts.append("</svg>")
    svg = "".join(parts)

    # легенда: карточка на каждый баг — номера совпадают с метками на схеме
    counts = {"P0": 0, "P1": 0, "P2": 0}
    cards: list[str] = []
    for f in findings or []:
        counts[f["severity"]] += 1
        color = SEVERITY_COLOR[f["severity"]]
        locs = ", ".join(sorted(b[:8] for b in f["where"]))
        card = (f'<div class="card" style="border-left:4px solid {color}">'
                f'<div class="card-head"><span class="num" style="background:{BUG_STROKE}">'
                f'{f["num"]}</span><span class="sev" style="background:{color}1a;'
                f'color:{color};border:1px solid {color}">{f["severity"]}</span>'
                f'<span class="card-title">{_esc(f["title"])}</span></div>'
                f'<div class="card-row"><b>Где:</b> блоки { _esc(locs) }</div>'
                f'<div class="card-row"><b>Баг:</b> {_esc(f["what"])}</div>')
        if f["fix"]:
            fx = f["fix"]
            card += f'<div class="card-row fix"><b>Исправление:</b> {_esc(fx["text"])}'
            if fx["where"]:
                card += (f'<br/>Менять блоки: '
                         + _esc(", ".join(sorted(b[:8] for b in fx["where"]))))
            if fx["insert_after"]:
                anchors = _esc(", ".join(sorted(b[:8] for b in fx["insert_after"])))
                card += f'<br/>Вставить новый блок после: {anchors} — «{_esc(fx["insert_title"])}»'
                if fx["insert_code"]:
                    card += f'<br/><code>{_esc(fx["insert_code"])}</code>'
            card += "</div>"
        cards.append(card + "</div>")
    legend = ("".join(cards) if findings else
              '<div class="card">Баги не размечены: схема построена только '
              'по структуре файла (режим навигации).</div>')

    head = title or path.name
    if findings:
        meta_tail = (f', багов: <b>P0 {counts["P0"]} · P1 {counts["P1"]}'
                     f' · P2 {counts["P2"]}</b>')
    else:
        meta_tail = ""
    meta = (f'блоков <b>{len(blocks)}</b>, связей <b>{len(edges)}</b>'
            f'{meta_tail} — источник: {_esc(path.name)}')
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{_esc(head)} — блок-схема</title>
<style>
:root {{ color-scheme: light; }}
body {{ margin: 0; padding: 18px; background: {PAGE_BG};
       font: 13px/1.5 "Segoe UI", system-ui, sans-serif; color: {TEXT_COLOR}; }}
h1 {{ font-size: 17px; margin: 0 0 4px; }}
.meta {{ color: {LABEL_COLOR}; margin-bottom: 12px; }}
.meta b {{ color: {TEXT_COLOR}; }}
.diagram {{ background: #fff; border: 1px solid #d6dae1; border-radius: 10px;
            overflow: auto; max-height: 78vh; }}
.legend {{ margin-top: 16px; display: grid; gap: 10px; }}
.card {{ background: #fff; border: 1px solid #d6dae1; border-radius: 8px;
         padding: 10px 12px; }}
.card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
.num {{ display: inline-flex; width: 22px; height: 22px; border-radius: 50%;
        color: #fff; font-weight: 700; align-items: center; justify-content: center; }}
.sev {{ font-size: 11px; font-weight: 700; border-radius: 999px; padding: 1px 8px; }}
.card-title {{ font-weight: 600; }}
.card-row {{ margin: 3px 0; }}
.card-row.fix {{ color: #0e9f5d; }}
code {{ font-family: Consolas, monospace; font-size: 12px;
        background: {PAGE_BG}; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body>
<h1>{_esc(head)}</h1>
<div class="meta">{meta}</div>
<div class="diagram">{svg}</div>
<div class="legend">{legend}</div>
</body>
</html>
"""


def _badge(cx: float, cy: float, num: int, color: str) -> str:
    return (f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="9" fill="{color}" '
            f'stroke="#fff" stroke-width="1.5"/><text x="{cx:.0f}" y="{cy + 3.5:.0f}" '
            f'text-anchor="middle" font-size="10" font-weight="700" fill="#fff">{num}</text>')


# -------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("process", type=Path)
    ap.add_argument("--findings", type=Path, default=None,
                    help="JSON с багами (схема — references/flow-diagram.md)")
    ap.add_argument("--out", type=Path, default=None,
                    help="выходной HTML (по умолчанию FILE.flow.html)")
    ap.add_argument("--title", default=None, help="заголовок страницы")
    ap.add_argument("--check", action="store_true",
                    help="сверить два рендера байт-в-байт (детерминизм)")
    args = ap.parse_args(argv)

    if not args.process.exists():
        print(f"FAIL: файл не найден: {args.process}", file=sys.stderr)
        return 2
    findings = None
    if args.findings:
        try:
            data = json.loads(args.findings.read_text(encoding="utf-8-sig"))
            blocks, _ = load_nodes(args.process)
            findings = validate_findings(data, blocks)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL: findings.json не читается: {exc}", file=sys.stderr)
            return 1
        except FindingError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    title = args.title or (f"{args.process.name} — баги" if findings
                           else f"{args.process.name} — структура")
    page1 = render(args.process, findings, title)
    if args.check and page1 != render(args.process, findings, title):
        print("FAIL: детерминизм нарушен — два рендера разошлись", file=sys.stderr)
        return 1

    out = args.out or args.process.with_suffix(".flow.html")
    out.write_text(page1, encoding="utf-8")
    if findings:
        by_sev = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_RANK}
        summary = (f"багов {len(findings)} "
                   f"(P0 {by_sev['P0']}, P1 {by_sev['P1']}, P2 {by_sev['P2']})")
    else:
        summary = "структура, без багов"
    print(f"OK: {out} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
