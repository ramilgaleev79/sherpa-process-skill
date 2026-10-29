#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Линт чужого `.process`: то, что в ревью находится машинно, а не глазами.

Сценарий ревью (references/reviewing-a-process.md) начинается с этого инструмента:
он отвечает на вопрос «что в файле объективно дефект», а `structure_audit.py` —
«где управление держится на GOTO-стрелках», `golden_inspector.py` — «что внутри
конкретного блока». Раньше эти проверки писались руками под задачу (`.review/lint.py`,
`.review/graph.py`), из-за чего один и тот же дефект то находился, то нет.

Проверки (код → серьёзность):

  LNT-SEC-001  секрет в открытом виде: пароль блока, `Bearer …`, `gsk_…`, `sk-…`,
               hex-токен ≥32, `token=` в URL                              P0
  LNT-SEC-002  `http://` во внешнем вызове (токен уходит открытым)        P1
  LNT-SEC-003  жёсткий сетевой путь (`\\\\host\\share…`) в свойстве         P2
  LNT-ID-001   дубли ID блоков                                            P0
  LNT-EDGE-001 связь на несуществующий блок (висячая)                     P0
  LNT-EDGE-002 `ErrorLevel=Handle`, но выход `Error` не подключён         P1
  LNT-EDGE-003 `IsPSVerified=false` (код блока не проверен)               P2
  LNT-GRF-001  блок недостижим от Start (мёртвый код)                     P2
  LNT-GRF-002  из блока нет пути к «Конец»/«Прервать» (обрыв потока)      P1
  LNT-GRF-003  одинаковые `InternalName`+`DisplayName` (копипаста)        P2
  LNT-UI-001   интерактивный блок (MessageBox, FolderBrowserDialog, …)    P2
  LNT-CAT-001  блок отсутствует в каталоге Designer                       P1
  LNT-CAT-002  версия блока старее каталога (legacy)                      P2
  LNT-VAR-001  ссылка на необъявленную переменную                         P2
  LNT-DIR-001  `#stoprefactoring` без парного `#startrefactoring`          P2
               в значении свойства: рефакторинг Designer выключен
               до конца этого выражения
  LNT-BAK-001  рядом лежит скрытый автосейв Designer `~<имя>`              P2
               (не авторский контент; мог разойтись с файлом)

Смысл серьёзностей — как в ревью OCR-проекта: P0 останавливает, P1 требует
решения, P2 — на сопровождение. Секреты и висячие связи перечисляются по
одному (их мало), остальное — счётчиком и списком до `--limit`.

Использование:
    python scripts/lint_process.py FILE.process [FILE ...] [--json] [--fail-on p0|p1|none]
    python scripts/lint_process.py OCR/main.process --limit 0

Код возврата: 0 — дефектов выбранного уровня нет, 1 — есть, 2 — файл не читается.
Только stdlib.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sherpa_xml.console import utf8_stdio  # noqa: E402
from sherpa_xml.types import text as text_type  # noqa: E402
from structure_audit import IGNORE_VARS, defuse, load as load_audit, model  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
CATALOG = SKILL_ROOT / "assets" / "block_index.json"
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

# Переменные рантайма Sherpa: не объявляются в диаграмме, но законно используются.
SHERPA_RUNTIME = IGNORE_VARS | {
    "ErrorMessage", "BusinessException", "SystemException", "ResponseRole",
    "TotalTokens", "CalledTools", "Result", "ItemIndex", "Item", "Row", "Index",
    "PathUnpack", "ProcessID", "ListSizes", "ScriptRoot", "CurrentDirectory",
    "CurrentBlockID", "Arguments",
    # константы и объекты рантайма: это не переменные проекта
    "True", "False", "Null", "html", "Tables", "item",
    "Word1", "WordDocument1", "Excel1", "ExcelWorkbook1", "__ExceptionBlockID",
}

# Блоки, которые в фоновом роботе некому закрыть.
INTERACTIVE = {"MessageBox", "InputBox", "FolderBrowserDialog", "SaveFileDialog", "OpenFileDialog"}

SECRET_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer-токен"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), "ключ Groq (gsk_…)"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "ключ OpenAI (sk-…)"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "hex-токен ≥32 символов"),
    (re.compile(r"[?&]token=[^&\"'\s)]+", re.I), "токен в URL"),
    (re.compile(r"(?i)(?:password|passwd|pwd|пароль)\s*[:=]\s*[^\s\"']{4,}"), "пароль в строке"),
]
HTTP_RE = re.compile(r"http://[^\s\"'<>]+", re.I)
LOCAL_HOST = re.compile(r"http://(?:localhost|127\.0\.0\.1|\[::1\])", re.I)
URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+@")


def _mask_url_credentials(text: str) -> str:
    """`http://user:pass@host` → `http://***@host`: секрет не попадает в отчёт."""
    return URL_CREDENTIALS.sub(r"\1***@", text)
UNC_RE = re.compile(r"\\\\[A-Za-z0-9._-]+\\[^\s\"'<>]*")
SECRET_PROP_NAMES = {"password", "token", "apikey", "api_key", "secret", "секрет", "пароль"}
# Значение-ссылка целиком ($x, $x.Y) — секрета в файле нет. PS-скрипт, начинающийся
# с `$x = …`, под fullmatch не попадает: образцы внутри тела выражения проверяются
# (ключи Groq/DaData в реальном ревью жили именно там).
VAR_REF = re.compile(r"\$[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def _catalog() -> dict:
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8"))["blocks"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}


def _decode(value: str) -> str:
    """Значение DiagramProperty → текст (Base64-контейнер или как есть)."""
    if not value:
        return ""
    try:
        return text_type.decode_lenient(value)
    except Exception:  # noqa: BLE001 — контейнер может быть не текстовым
        return value


def _read_variables(path: Path) -> list[dict]:
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    out = []
    for prop in root.findall(".//DiagramProperty"):
        out.append({
            "name": prop.get("Name") or "",
            "type": prop.get("Type") or "",
            "scope": prop.get("IsGlobal") or "",
            "value": _decode(prop.get("Value") or ""),
            "default": _decode(prop.get("DefaultValue") or ""),
        })
    return out


def _item_flags(path: Path) -> dict[str, dict]:
    """IsPSVerified / IsEnabled / Description по ID блока (structure_audit их не читает)."""
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    out = {}
    for item in root.iter("DesignerItem"):
        bid = item.findtext("ID")
        out[bid] = {
            "ps_verified": (item.findtext("IsPSVerified") or "true").lower() != "false",
            "enabled": (item.findtext("IsEnabled") or "true").lower() != "false",
            "description": (item.findtext("Description") or "").strip(),
        }
    return out


def _finding(code: str, severity: str, location: str, message: str) -> dict:
    return {"code": code, "severity": severity, "location": location, "message": message}


def _looks_like_secret(name: str, value: str) -> str | None:
    """Секрет в значении свойства: по имени свойства и по образцу значения."""
    if not value or VAR_REF.fullmatch(value):
        return None                      # значение — ссылка на переменную: секрета в файле нет
    lowered = name.lower()
    if lowered in SECRET_PROP_NAMES or "password" in lowered or "пароль" in lowered:
        return "значение свойства с секретным именем"
    for pattern, title in SECRET_PATTERNS:
        if pattern.search(value):
            return title
    return None


def lint(path: Path) -> dict:
    blocks, edges = load_audit(path)
    flags = _item_flags(path)
    variables = _read_variables(path)
    catalog = _catalog()
    findings: list[dict] = []

    # --- структура: дубли ID и висячие связи (считаются по сырому файлу) ---
    raw = path.read_bytes().decode("utf-8-sig")
    root = ET.fromstring(raw[raw.find("<Root>"):])
    items_section = root.find("DesignerItems")
    if items_section is None:
        raise ValueError("в файле нет секции DesignerItems — это не .process")
    ids = [it.findtext("ID") for it in items_section]
    for bid, count in Counter(ids).items():
        if count > 1:
            findings.append(_finding("LNT-ID-001", "P0", f"блок {bid[:8]}",
                                     f"ID встречается {count} раза — связи неоднозначны"))
    for src, sport, dst, dport in edges:
        for side, value in (("source", src), ("sink", dst)):
            if value not in blocks:
                findings.append(_finding("LNT-EDGE-001", "P0", f"связь {src[:8]}→{dst[:8]}",
                                         f"{side}-блок {value[:8]} не существует — связь висячая"))

    # --- граф: достижимость, обрывы, копипаста ---
    m = model(blocks, edges)
    for bid in m["dead"]:
        block = blocks[bid]
        if (block["internal"] or "") != "Comment":
            findings.append(_finding("LNT-GRF-001", "P2", f"блок {bid[:8]} {block['internal']}",
                                     f"«{block['display'][:40]}» недостижим от Start — мёртвый код"))
    for bid in m["no_stop"]:
        block = blocks[bid]
        # AbortContainer — намеренный `pop` стека (GOTO-эквивалент), а не обрыв потока
        if block["internal"] in ("ReturnOrStop", "FullAbort", "AbortContainer"):
            continue
        findings.append(_finding("LNT-GRF-002", "P1", f"блок {bid[:8]} {block['internal']}",
                                 f"из «{block['display'][:40]}» нет пути к «Конец»/«Прервать»"))
    groups = Counter((b["internal"], b["display"]) for b in blocks.values())
    dup_groups = {k: v for k, v in groups.items() if v > 1}
    if dup_groups:
        total = sum(dup_groups.values())
        top = ", ".join(f"{k[0]} «{k[1][:22]}»×{v}"
                        for k, v in sorted(dup_groups.items(), key=lambda x: -x[1])[:4])
        findings.append(_finding("LNT-GRF-003", "P2", "подписи блоков",
                                 f"{total} блоков в {len(dup_groups)} группах с одинаковой "
                                 f"подписью (копипаста): {top}"))
    disabled = [bid for bid, b in blocks.items() if not b["enabled"]]
    if disabled:
        names = ", ".join(sorted({blocks[bid]["internal"] for bid in disabled})[:8])
        findings.append(_finding("LNT-EDGE-004", "P2", "выключенные блоки",
                                 f"{len(disabled)} блоков с IsEnabled=false ({names}) — "
                                 f"проверить, не оставлены ли они в потоке по ошибке"))

    # --- обработка ошибок и PS-код ---
    out_ports = defaultdict(set)
    for src, sport, _dst, _dport in edges:
        out_ports[src].add(sport)
    for bid, block in blocks.items():
        if block["props"].get("ErrorLevel") == "Handle" and "Error" not in out_ports[bid]:
            findings.append(_finding("LNT-EDGE-002", "P1", f"блок {bid[:8]} {block['internal']}",
                                     "ErrorLevel=Handle, но выход Error не подключён: ошибка "
                                     "потеряется молча"))
        info = flags.get(bid, {})
        if not info.get("ps_verified", True):
            findings.append(_finding("LNT-EDGE-003", "P2", f"блок {bid[:8]} {block['internal']}",
                                     "IsPSVerified=false — код блока не проверен Designer'ом"))
        if block["internal"] in INTERACTIVE:
            findings.append(_finding("LNT-UI-001", "P2", f"блок {bid[:8]} {block['internal']}",
                                     "интерактивный блок: в неинтерактивном запуске диалог "
                                     "некому закрыть"))

    # --- директивы рефакторинга и автосейвы Designer (IL-verified) ---
    from sherpa_xml.designer_refactor import directives_unbalanced
    for it in items_section:
        content = it.findtext("Content") or ""
        if "#stoprefactoring" not in content and "#startrefactoring" not in content:
            continue
        bid = (it.findtext("ID") or "")[:8]
        # директива живёт в значениях свойств (Code) — проверяем каждое
        inner = ET.fromstring(content) if content else None
        codes = []
        if inner is not None:
            for coll in inner.iter():
                if coll.tag.endswith("ToolboxItemSettings.Properties"):
                    for props in coll:
                        for prop in props:
                            codes.append(prop.get("Value") or "")
        for code in codes:
            if directives_unbalanced(code):
                findings.append(_finding("LNT-DIR-001", "P2", f"блок {bid}",
                                         "`#stoprefactoring` без парного `#startrefactoring`: "
                                         "рефакторинг Designer (переименование переменных) "
                                         "выключен до конца этого значения свойства"))
                break
    autosave = path.with_name("~" + path.name)
    if autosave.exists():
        findings.append(_finding("LNT-BAK-001", "P2", str(autosave),
                                 "рядом скрытый автосейв Designer (~имя): не авторский "
                                 "контент, состояние могло разойтись с файлом — в ревью "
                                 "не подставлять, при правке удалить/перезаписать открытием "
                                 "в Designer"))

    # --- каталог: неизвестные блоки и старые версии ---
    if catalog:
        for bid, block in blocks.items():
            internal = block["internal"]
            if internal and internal not in catalog:
                findings.append(_finding("LNT-CAT-001", "P1", f"блок {bid[:8]} {internal}",
                                         "блока нет в каталоге Designer — процесс не "
                                         "воспроизводится в текущей версии"))
        legacy: Counter = Counter()
        for it in root.iter("DesignerItem"):
            content = it.findtext("Content") or ""
            if not content:
                continue
            inner = ET.fromstring(content)
            internal, version = inner.get("InternalName"), inner.get("Version")
            entry = catalog.get(internal or "")
            if entry and version and str(entry.get("version")) != str(version):
                legacy[(internal, str(version), str(entry["version"]))] += 1
        if legacy:
            total = sum(legacy.values())
            top = ", ".join(f"{k[0]} {k[1]}→{k[2]}×{n}"
                            for k, n in legacy.most_common(4))
            findings.append(_finding("LNT-CAT-002", "P2", "версии блоков",
                                     f"{total} блоков старее каталога ({len(legacy)} типов): {top} — "
                                     f"структуру читать можно, свойства сверять с текущим шаблоном"))

    # --- секреты и внешние адреса ---
    for bid, block in blocks.items():
        for name, value in block["props"].items():
            if not value:
                continue
            secret = _looks_like_secret(name, value)
            if secret:
                findings.append(_finding("LNT-SEC-001", "P0", f"блок {bid[:8]} {block['internal']}",
                                         f"{name}: {secret} — значение нужно вынести в "
                                         f"учётные данные/переменную робота"))
            for match in HTTP_RE.finditer(value):
                local = bool(LOCAL_HOST.match(match.group(0)))
                findings.append(_finding(
                    "LNT-SEC-002", "P2" if local else "P1",
                    f"блок {bid[:8]} {block['internal']}",
                    f"http:// вместо https:// — "
                    f"{_mask_url_credentials(match.group(0)[:60])}"))
            for match in UNC_RE.finditer(value):
                findings.append(_finding("LNT-SEC-003", "P2", f"блок {bid[:8]} {block['internal']}",
                                         f"жёсткий сетевой путь {match.group(0)[:60]}"))
    for var in variables:
        for field in ("value", "default"):
            secret = _looks_like_secret(var["name"], var[field])
            if secret:
                findings.append(_finding("LNT-SEC-001", "P0", f"переменная {var['name']}",
                                         f"{field}: {secret}"))

    # --- данные: необъявленные переменные ---
    declared = {v["name"] for v in variables}
    writes, reads = defuse(blocks)
    used = set(reads) | {v for v in writes if v in reads}
    undeclared = sorted(v for v in used if v not in declared and v not in SHERPA_RUNTIME)
    if undeclared:
        findings.append(_finding("LNT-VAR-001", "P2", "переменные",
                                 f"{len(undeclared)} ссылок на необъявленные переменные: "
                                 + ", ".join("$" + v for v in undeclared[:12])
                                 + (" …" if len(undeclared) > 12 else "")))

    counts = Counter(f["severity"] for f in findings)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["code"], f["location"]))
    return {
        "file": str(path),
        "name": path.name,
        "blocks": len(blocks),
        "edges": len(edges),
        "variables": len(variables),
        "findings": findings,
        "summary": {s: counts.get(s, 0) for s in ("P0", "P1", "P2")},
        "structure": {
            "kinds": {k: len(v) for k, v in m["kinds"].items()},
            "hubs": len(m["hubs"]),
            "depth": m["depth"],
        },
        "catalog_checked": bool(catalog),
    }


def report(info: dict, limit: int = 12) -> None:
    print("=" * 100)
    print(f"{info['name']}: блоков {info['blocks']}, связей {info['edges']}, "
          f"переменных {info['variables']}")
    kinds = info["structure"]["kinds"]
    goto = sum(v for k, v in kinds.items() if k.startswith("goto"))
    print(f"структура: GOTO-подобных стрелок {goto} "
          f"(из них назад {kinds.get('goto-back', 0)}), хабы {info['structure']['hubs']}; "
          f"остальное — structure_audit.py")
    if not info["catalog_checked"]:
        print("каталог недоступен: проверки LNT-CAT-* пропущены")
    titles = {"P0": "P0 (критично)", "P1": "P1 (важно)", "P2": "P2 (сопровождение)"}
    for severity in ("P0", "P1", "P2"):
        rows = [f for f in info["findings"] if f["severity"] == severity]
        if not rows:
            continue
        print(f"\n{titles[severity]}: {len(rows)}")
        shown = rows if limit <= 0 else rows[:limit]
        for f in shown:
            print(f"  [{f['code']}] {f['location']}: {f['message']}")
        if limit > 0 and len(rows) > limit:
            print(f"  … ещё {len(rows) - limit} (--limit 0 — показать все)")
    s = info["summary"]
    print(f"\nИТОГ: P0 {s['P0']}, P1 {s['P1']}, P2 {s['P2']}")


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Линт .process: дефекты, секреты, каталог")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="машинный отчёт")
    ap.add_argument("--fail-on", choices=["p0", "p1", "none"], default="p0",
                    help="какой уровень считать провалом (по умолчанию p0)")
    ap.add_argument("--limit", type=int, default=12, help="сколько строк на уровень (0 — все)")
    args = ap.parse_args(argv)

    reports = []
    for path in args.files:
        if not path.exists():
            print(f"нет файла: {path}", file=sys.stderr)
            return 2
        try:
            info = lint(path)
        except (ET.ParseError, UnicodeDecodeError, ValueError, TypeError, AttributeError) as exc:
            print(f"{path.name}: FAIL — файл не читается: {exc}", file=sys.stderr)
            return 2
        reports.append(info)
        if not args.json:
            report(info, args.limit)

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=1))

    limit = {"none": -1, "p0": 0, "p1": 1}[args.fail_on]
    if limit < 0:
        return 0
    bad = any(info["summary"][s] for info in reports
              for s in ("P0", "P1")[: limit + 1])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
