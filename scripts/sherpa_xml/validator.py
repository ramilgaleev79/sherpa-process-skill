"""Валидатор (фаза 5): Python-слой — «всё умное» (PLAN.md D3).

Два входа:
- validate_ir(ir, profile)  — проверка IR до генерации (SEM-*, SHP-граф);
- validate_process(path)    — проверка готового .process (SHP-ID, SEM-SER).

Отчёт — машинный: [{code, severity, location, message}], status = passed|failed.
Коды семейств: main.md §21–22, schematron-документ; каждый assert валиден
и для valid-, и для invalid-фикстуры (tests/unit/test_validator.py).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from . import profiles
from .ir import NODE_TYPES, ProcessIR
from .types import collections, data_table, primitives, text

# Hashtable*-блоки: main.md §12.2 confirmed, но golden-образца нет (tests GOLDEN_MISSING)
UNCONFIRMED_PHYSICAL = frozenset(
    {NODE_TYPES[k] for k in ("CollectionClear", "CollectionSetItem", "CollectionRemoveItem",
                             "CollectionContainsKey", "CollectionContainsValue")}
)

CONDITION_KINDS_LOGICAL = frozenset({"compare", "logical"})
EXPR_RESULT_TYPES = {
    "number_literal": "Number",
    "boolean_literal": "Logical",
    "string_literal": "Text",
    "list_literal": "List",
    "hashtable_literal": "Hashtable",
    "data_table_literal": "DataTable",
    "secure_encode": "SecureString",
    "secure_decode": "Text",
    "compare": "Logical",
    "logical": "Logical",
}


@dataclass
class Issue:
    code: str
    severity: str  # FATAL | ERROR | WARN
    location: str
    message: str
    rule: str  # словарное имя для профиля (unresolved_todo, …)


def _walk_expr(expr, out: list):
    out.append(expr)
    for side in ("left", "right"):
        sub = expr.get(side)
        if isinstance(sub, dict):
            _walk_expr(sub, out)


def _expr_variables(node: dict, field: str) -> list[str]:
    expr = node.get(field)
    if not expr:
        return []
    flat: list[dict] = []
    _walk_expr(expr, flat)
    return [e["variable"] for e in flat if e.get("kind") == "variable_reference" and e.get("variable")]


def _expr_static_type(expr: dict) -> str | None:
    kind = expr.get("kind")
    if kind in EXPR_RESULT_TYPES:
        return EXPR_RESULT_TYPES[kind]
    return None  # powershell / arithmetic / variable_reference — контекстно


def _while_body_keys(node: dict, edges: list) -> set[str]:
    """Узлы тела цикла: от Iteration-ребра до возврата в while.Input (упрощённо — по цепочке Next)."""
    entry = next((e["to"] for e in edges if e["from"] == node["key"] and e["from_port"] == "Iteration"), None)
    body: set[str] = set()
    cur = entry
    while cur and cur not in body:
        body.add(cur)
        cur = next((e["to"] for e in edges if e["from"] == cur and e["from_port"] == "Next" and e["to"] != node["key"]), None)
    return body


def validate_ir(parsed: ProcessIR, profile: str = "draft") -> dict:
    """Полная IR-валидация под профиль; возвращает {status, profile, issues}."""
    issues: list[Issue] = []
    nodes = parsed["nodes"]
    edges = parsed["edges"]
    variables = {v["name"]: v for v in parsed["variables"]}
    externals = {e.get("name") for e in parsed["external_variables"]}
    forbidden = profiles.blocking_codes(profile)
    node_by_key = {n["key"]: n for n in nodes}

    def emit(rule: str, code: str, severity: str, location: str, message: str) -> None:
        # forbidden-семантика: коды-«нарушители» эмитятся как ERROR всегда,
        # но блокируют только если правило запрещено профилем (иначе WARN).
        if rule in forbidden:
            issues.append(Issue(code, "ERROR", location, message, rule))
        elif rule in profiles.WARN_ONLY_IN_DRAFT:
            issues.append(Issue(code, "WARN", location, message, rule))
        else:
            issues.append(Issue(code, severity, location, message, rule))

    # --- SHP-GRF: граф ---
    keys = {n["key"] for n in nodes}
    start = next((n for n in nodes if n["type"] == "Start"), None)
    if start:
        reachable: set[str] = set()
        frontier = [start["key"]]
        while frontier:
            cur = frontier.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            frontier.extend(e["to"] for e in edges if e["from"] == cur)
        for n in nodes:
            if n["key"] not in reachable:
                issues.append(Issue("SHP-GRF-010", "ERROR", f"node {n['key']}", "блок недостижим от Start", "unreachable"))
    stops = {n["key"] for n in nodes if n["type"] == "Stop"}
    if stops:
        # обратная достижимость до Stop
        to_stop: set[str] = set(stops)
        frontier = list(stops)
        while frontier:
            cur = frontier.pop()
            for e in edges:
                if e["to"] == cur and e["from"] not in to_stop:
                    to_stop.add(e["from"])
                    frontier.append(e["from"])
        for n in nodes:
            if n["key"] not in to_stop and n["type"] not in ("Stop", "AbortProcess", "AbortContainer"):
                issues.append(Issue("SHP-GRF-011", "ERROR", f"node {n['key']}", "нет пути к Stop/терминальному блоку", "no_path_to_stop"))
    seen_edges = set()
    for e in edges:
        sig = (e["from"], e["from_port"], e["to"], e["to_port"])
        if sig in seen_edges:
            issues.append(Issue("SHP-GRF-012", "ERROR", f"edge {sig}", "дублирующая связь", "duplicate_edge"))
        seen_edges.add(sig)

    # --- SHP-TODO / SHP-BLK ---
    for n in nodes:
        if n["type"] == "ToDo":
            emit("unresolved_todo", "SHP-TODO-001", "WARN" if profile == "draft" else "ERROR",
                 f"node {n['key']}", f"ToDo-плейсхолдер: {n.get('unresolved_action')}")
        if NODE_TYPES.get(n["type"]) in UNCONFIRMED_PHYSICAL:
            emit("unconfirmed_collection_block", "SHP-BLK-010", "WARN", f"node {n['key']}",
                 f"блок {n['type']} не подтверждён golden (нет шаблона)")

    # --- SEM-REF: переменные в выражениях ---
    declared = set(variables)
    for n in nodes:
        refs = _expr_variables(n, "condition")
        for a in n.get("assignments", []):
            refs += _expr_variables(a, "expression")
        for r in refs:
            if r not in declared and r not in externals:
                emit("variable_undeclared", "SEM-REF-001", "WARN", f"node {n['key']}",
                     f"переменная {r!r} не объявлена и не внешняя")

    # --- SEM-EXPR / SEM-TYPE ---
    for n in nodes:
        if n["type"] in ("Condition", "WhileLoop"):
            c = n["condition"]
            ck = c.get("kind")
            if profile == "strict" and ck not in CONDITION_KINDS_LOGICAL:
                if not (ck == "variable_reference" and variables.get(c.get("variable"), {}).get("sherpa_type") == "Logical"):
                    emit("untyped_expression_in_condition", "SEM-EXPR-011", "ERROR", f"node {n['key']}",
                         "условие должно быть compare/logical/ссылкой на Logical")
            if ck == "variable_reference":
                vt = variables.get(c.get("variable"), {}).get("sherpa_type")
                if vt not in (None, "Logical"):
                    emit("implicit_truthiness", "SEM-EXPR-010", "WARN", f"node {n['key']}",
                         f"неявная истинность: {c.get('variable')} имеет тип {vt}, а не Logical")
            if profile == "strict" and ck == "powershell":
                emit("freeform_powershell", "SEM-EXPR-012", "ERROR", f"node {n['key']}", "freeform PowerShell запрещён в strict")
        if n["type"] == "Assign":
            for i, a in enumerate(n.get("assignments", []), start=1):
                if profile == "strict" and a["expression"].get("kind") == "powershell":
                    emit("freeform_powershell", "SEM-EXPR-012", "ERROR", f"node {n['key']}.Expression{i}", "freeform PowerShell запрещён в strict")
                target = variables.get(a["target"])
                if target:
                    want = target["sherpa_type"]
                    got = _expr_static_type(a["expression"])
                    if got and got != want:
                        sev = "ERROR" if "ambiguous_type_coercion" in forbidden else "WARN"
                        issues.append(Issue("SEM-TYPE-001", sev, f"node {n['key']}.Expression{i}",
                                            f"тип выражения {got} не совпадает с типом {a['target']} ({want})",
                                            "ambiguous_type_coercion"))

    # --- SEM-LOOP: эвристики While (§17.7) ---
    for n in nodes:
        if n["type"] != "WhileLoop":
            continue
        body = _while_body_keys(n, edges)
        if not body:
            issues.append(Issue("SEM-LOOP-001", "WARN", f"node {n['key']}", "пустое тело цикла", "empty_body"))
        useful = {"Assign", "InvokeExpression"} | {k for k in NODE_TYPES if k.startswith(("List", "Table", "Collection"))}
        if body and not any(node_by_key[k]["type"] in useful for k in body):
            issues.append(Issue("SEM-LOOP-002", "WARN", f"node {n['key']}",
                                "тело цикла не содержит блоков, меняющих состояние", "body_no_state_change"))
        cond = n["condition"]
        if cond.get("kind") == "compare":
            lt, rt = cond.get("left", {}), cond.get("right", {})
            if lt.get("kind") == "number_literal" and rt.get("kind") == "number_literal":
                ops = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "lt": lambda a, b: a < b,
                       "le": lambda a, b: a <= b, "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b}
                op = ops.get(cond.get("operator"))
                if op and op(lt["value"], rt["value"]):
                    issues.append(Issue("SEM-LOOP-003", "WARN", f"node {n['key']}",
                                        "условие статически всегда истинно — бесконечный цикл", "always_true"))

    # --- SHP-VAR-008: неиспользуемые переменные ---
    used: set[str] = set()
    for n in nodes:
        used.update(_expr_variables(n, "condition"))
        for a in n.get("assignments", []):
            used.add(a["target"])
            used.update(_expr_variables(a, "expression"))
    for v in parsed["variables"]:
        if v["name"] not in used:
            issues.append(Issue("SHP-VAR-008", "WARN", f"variable {v['name']}", "переменная не используется", "unused_variable"))

    blocking = [i for i in issues if i.severity in profiles.BLOCKING_SEVERITIES]
    return {
        "status": "failed" if blocking else "passed",
        "profile": profile,
        "issues": [asdict(i) for i in issues],
    }


# --- .process-слой (готовый файл: golden или сгенерированный) ---


def validate_process(path: Path | str) -> dict:
    issues: list[Issue] = []
    try:
        raw = Path(path).read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            issues.append(Issue("SHP-DOC-000", "WARN", "file", "нет UTF-8 BOM (формат golden — с BOM)", "no_bom"))
        root = ET.fromstring(raw.decode("utf-8-sig"))
    except ET.ParseError as exc:
        return {"status": "failed", "profile": "process",
                "issues": [asdict(Issue("SHP-DOC-001", "FATAL", "file", f"XML не парсится: {exc}", "not_well_formed"))]}

    ids = {i.findtext("ID") for i in root.findall(".//DesignerItem")}
    for c in root.findall("./Connections/Connection"):
        if c.findtext("SourceID") not in ids:
            issues.append(Issue("SHP-ID-004", "ERROR", "Connections", "SourceID ссылается на несуществующий блок", "dangling_source"))
        if c.findtext("SinkID") not in ids:
            issues.append(Issue("SHP-ID-005", "ERROR", "Connections", "SinkID ссылается на несуществующий блок", "dangling_sink"))

    names = set()
    for p in root.findall(".//DiagramProperty"):
        name, vtype = p.get("Name"), p.get("Type")
        dv = p.get("DefaultValue", "")
        if name in names:
            issues.append(Issue("SHP-VAR-002", "ERROR", f"variable {name}", "дублирующееся имя переменной", "dup_var"))
        names.add(name)
        try:
            if vtype == "Text" and dv:
                text.decode(dv)
            elif vtype == "List" and dv:
                collections.decode_list(dv)
            elif vtype == "Hashtable" and dv:
                collections.decode_hashtable(dv)
            elif vtype == "DataTable" and dv:
                data_table.decode(dv)
            elif vtype == "Logical" and dv not in ("True", "False"):
                issues.append(Issue("SHP-VAR-006", "ERROR", f"variable {name}", "Logical хранит не True/False", "bad_logical"))
        except ValueError as exc:
            issues.append(Issue("SEM-SER-001", "ERROR", f"variable {name}", f"контейнер не читается: {exc}", "bad_container"))

    blocking = [i for i in issues if i.severity in profiles.BLOCKING_SEVERITIES]
    return {"status": "failed" if blocking else "passed", "profile": "process",
            "issues": [asdict(i) for i in issues]}
