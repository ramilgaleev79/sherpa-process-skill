"""Process IR — промежуточное представление для генерации .process Sherpa RPA.

IR — единственное, что пишет агент (LLM); XML генерирует компилятор (фаза 4).
Контракт закрытый: неизвестные ключи/типы узлов/типы переменных — ошибка,
чтобы IR не расползался за пределы подтверждённого golden-набора.

Детали сериализации — references/.
Pure stdlib (D1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# --- Подтверждённые множества ---
# Типы свойств и набор блоков сняты с установленного Designer (MainToolbox.xaml, 752 блока);
# 50 исходных шаблонов подтверждены golden-диаграммами, остальные — каталогом продукта.
# references/type-catalog.yaml — что наблюдалось в реальных процессах.

VARIABLE_TYPES = frozenset(
    {
        "Number", "Logical", "Text", "SecureString", "Hashtable", "Dictionary", "List", "DataTable",
        # добавлены по каталогу продукта (Toolboxes-ru.json / settings.json → PropertyTypes):
        "Object", "DateTime", "Money",
    }
)

EXPRESSION_KINDS = frozenset(
    {
        "variable_reference",
        "number_literal",
        "boolean_literal",
        "string_literal",
        "compare",
        "logical",
        "arithmetic",
        "powershell",
        "secure_encode",
        "secure_decode",
        # DateTime — плоская строка формата Designer (наблюдено: 05.06.2023 18:48:20)
        "datetime_literal",
        # литералы начальных значений коллекций (writer'ы фазы 3, golden-подтверждены)
        "list_literal",
        "hashtable_literal",
        "data_table_literal",
    }
)

# Логические типы узлов IR → физические InternalName .
NODE_TYPES: dict[str, str] = {
    # ядро графа
    "Start": "Start",
    "Stop": "ReturnOrStop",
    "ToDo": "ToDoBlock",
    "Assign": "Assign",
    "Condition": "IfCondition",
    "WhileLoop": "While",
    "ForLoop": "For",
    "TryCatch": "TryCatchContainer",
    "Container": "CollapseContainer",
    "AbortProcess": "FullAbort",
    "AbortContainer": "AbortContainer",
    "InvokeExpression": "InvokeExpression",
    "Delay": "Delay",
    # Hashtable/Dictionary (общие физические блоки Hashtable*)
    "CollectionClear": "HashtableClear",
    "CollectionSetItem": "HashtableAddItem",
    "CollectionRemoveItem": "HashtableRemoveItem",
    "CollectionContainsKey": "HashtableContainsKey",
    "CollectionContainsValue": "HashtableContainsValue",
    # List
    "ListClear": "ListClear",
    "ListAddItem": "ListAddItem",
    "ListInsertItem": "ListInsertItem",
    "ListRemoveItem": "ListRemoveItem",
    "ListRemoveAtItem": "ListRemoveAtItem",
    "ListRemoveDuplicates": "ListRemoveDuplicates",
    "ListSort": "ListSort",
    "ListContains": "ListContains",
    "ListLoadFromFile": "ListLoadFromFile",
    "ListSaveToFile": "ListSaveToFile",
    # DataTable (физические имена семантичны)
    "TableGetCellValue": "GetCellValue",
    "TableSetCellValue": "SetCellValue",
    "TableAddColumn": "AddColumn",
    "TableAddRow": "AddRow",
    "TableClearRows": "ClearRows",
    "TableRemoveRow": "RemoveRow",
    "TableRemoveColumn": "RemoveColumn",
    "TableRenameColumn": "RenameColumn",
    "TableChangeTypeColumn": "ChangeTypeColumn",
    "TableSort": "SortDataTable",
    "TableDistinct": "DistinctDataTable",
    "TableMerge": "MergeDataTable",
    "TableSelectRows": "SelectRows",
    "TableContainsValue": "ContainsValue",
    "TableFindRow": "FindRow",
    "TableContainsColumn": "ContainsColumn",
    "TableRemoveEmpty": "RemoveEmptyRowColumn",
    "TableGetRange": "GetDataTableRange",
    "TableInsertTable": "InsertDataTable",
    "TableTranspose": "TransposeDataTable",
    "TableLoadCsv": "LoadCSVDataTable",
    "TableSaveCsv": "SaveCSVDataTable",
    "TableToText": "DataTableToText",
    "TableToHashtable": "DataTableToHashtable",
    "TableToList": "DataTableToList",
    "ListToTable": "ListToDataTable",
}

SCOPES = frozenset({"local", "global"})
MODES = frozenset({"draft", "release", "strict"})

# --- Каталог блоков (assets/block_index.json, генерируется scripts/dev/harvest_catalog.py) ---
# NODE_TYPES остаётся набором удобных алиасов для типовых конструкций, но НЕ ограничивает
# генерацию: тип узла может быть любым InternalName из установленного Designer.

_BLOCK_INDEX_PATH = Path(__file__).resolve().parents[2] / "assets" / "block_index.json"


def _load_block_index() -> dict:
    try:
        return json.loads(_BLOCK_INDEX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"meta": {}, "blocks": {}}


BLOCK_INDEX: dict = _load_block_index()
BLOCK_NAMES: frozenset[str] = frozenset(BLOCK_INDEX.get("blocks", {}))


def resolve_physical(node_type: str) -> str | None:
    """Тип узла IR → InternalName блока. None — тип неизвестен каталогу."""
    alias = NODE_TYPES.get(node_type)
    if alias is not None:
        return alias
    return node_type if node_type in BLOCK_NAMES else None

COMPARE_OPS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "like", "match"})
LOGICAL_OPS = frozenset({"and", "or", "not", "xor"})
ARITHMETIC_OPS = frozenset({"add", "sub", "mul", "div", "mod", "pow"})

_EXPR_KEYS = frozenset({"kind", "variable", "value", "operator", "left", "right"})
_NODE_KEYS = frozenset(
    {
        "key",
        "type",
        "purpose",
        "display_name",
        "unresolved_action",
        "expression",
        "assignments",
        "condition",
        "branches",
        "body",
        "next",
        "start_index",
        "end_index",
        "step",
        "index_variable",
        "properties",
    }
)
_VAR_KEYS = frozenset({"key", "name", "sherpa_type", "scope", "initial"})
_EDGE_KEYS = frozenset({"from", "from_port", "to", "to_port"})


class IRError(ValueError):
    """Ошибка схемы IR: неизвестный ключ, тип, висячая ссылка и т.п."""


def _check_keys(data: dict, allowed: frozenset, where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise IRError(f"{where}: неизвестные ключи {sorted(unknown)}; допустимо {sorted(allowed)}")


# --- Выражения (§19) ---
# secure_encode/secure_decode: аргумент хранится в left, как у унарных операций.


class Expr(dict):
    """Выражение IR — строгий словарь с валидацией на создании.

    Хранится как dict для каноничного JSON round-trip без потери порядка ключей.
    """

    def __init__(self, data: dict):
        _check_keys(data, _EXPR_KEYS, "expression")
        kind = data.get("kind")
        if kind not in EXPRESSION_KINDS:
            raise IRError(f"expression: неизвестный kind {kind!r}; допустимо {sorted(EXPRESSION_KINDS)}")
        super().__init__(data)
        self._validate()

    def _validate(self) -> None:
        kind = self["kind"]
        if kind == "variable_reference" and not self.get("variable"):
            raise IRError("variable_reference: обязателен 'variable'")
        if kind == "string_literal":
            if not isinstance(self.get("value"), str):
                raise IRError("string_literal: value должен быть строкой (сырой текст, без PS-кавычек)")
        if kind == "number_literal":
            # bool — подкласс int в Python; для Number это ошибка, а не «1/0».
            if not isinstance(self.get("value"), (int, float)) or isinstance(self.get("value"), bool):
                raise IRError("number_literal: value должен быть числом (bool не допускается)")
        if kind == "boolean_literal":
            if not isinstance(self.get("value"), bool):
                raise IRError("boolean_literal: value должен быть True/False")
        if kind == "datetime_literal":
            v = self.get("value")
            if not isinstance(v, str) or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}", v):
                raise IRError("datetime_literal: value должен быть строкой dd.MM.yyyy HH:mm:ss")
        if kind == "powershell" and not str(self.get("value") or "").strip():
            raise IRError("powershell: value должен быть непустой строкой")
        if kind in ("compare", "logical", "arithmetic"):
            ops = {"compare": COMPARE_OPS, "logical": LOGICAL_OPS, "arithmetic": ARITHMETIC_OPS}[kind]
            if self.get("operator") not in ops:
                raise IRError(f"{kind}: оператор {self.get('operator')!r} не входит в {sorted(ops)}")
            if self["operator"] == "not":
                if "left" not in self:
                    raise IRError("logical not: аргумент в 'left'")
            elif "left" not in self or "right" not in self:
                raise IRError(f"{kind}: нужны left и right")
        if kind in ("secure_encode", "secure_decode") and "left" not in self:
            raise IRError(f"{kind}: аргумент в 'left'")
        if kind == "list_literal":
            if not isinstance(self.get("value"), list) or not all(isinstance(x, str) for x in self["value"]):
                raise IRError("list_literal: value должен быть списком строк (List<String>)")
        if kind == "hashtable_literal":
            v = self.get("value")
            if not isinstance(v, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in v
            ):
                raise IRError("hashtable_literal: value = список пар [ключ, значение] строк")
        if kind == "data_table_literal":
            v = self.get("value")
            if not isinstance(v, dict) or "columns" not in v or "rows" not in v:
                raise IRError("data_table_literal: value = {columns: [[имя, тип],…], rows: [{колонка: значение},…]}")
            columns, rows = v["columns"], v["rows"]
            if not isinstance(columns, list) or not all(
                isinstance(c, (list, tuple)) and len(c) == 2 and all(isinstance(x, str) for x in c)
                for c in columns
            ):
                raise IRError("data_table_literal.columns: список пар [имя, тип] из строк")
            if not isinstance(rows, list) or not all(
                isinstance(r, dict)
                and all(isinstance(k, str) and isinstance(val, str) for k, val in r.items())
                for r in rows
            ):
                # None/числа здесь не поддерживаются: DBNull кодируется ОТСУТСТВИЕМ
                # ключа (references/datatable-serialization.md), а не значением null.
                raise IRError(
                    "data_table_literal.rows: строки должны быть объектами {колонка: строка}; "
                    "для DBNull не указывайте ключ (null не допускается)"
                )

        for side in ("left", "right"):
            if side in self and not isinstance(self[side], Expr):
                raise IRError(f"expression.{side}: должен быть выражением")

    @classmethod
    def parse(cls, data: object) -> "Expr":
        if not isinstance(data, dict):
            raise IRError("expression: должен быть объектом")
        parsed = {k: (cls.parse(v) if k in ("left", "right") else v) for k, v in data.items()}
        return cls(parsed)


# --- Сущности ---


class Variable(dict):
    def __init__(self, data: dict):
        _check_keys(data, _VAR_KEYS, "variable")
        if not data.get("key") or not data.get("name"):
            raise IRError("variable: обязательны key и name")
        if data.get("sherpa_type") not in VARIABLE_TYPES:
            raise IRError(f"variable {data.get('name')!r}: тип {data.get('sherpa_type')!r} не подтверждён")
        if data.get("scope", "local") not in SCOPES:
            raise IRError(f"variable {data['name']!r}: scope должен быть local|global")
        if "initial" in data and data["initial"] is not None and not isinstance(data["initial"], Expr):
            raise IRError(f"variable {data['name']!r}: initial должен быть выражением")
        super().__init__(data)


class Node(dict):
    def __init__(self, data: dict):
        _check_keys(data, _NODE_KEYS, "node")
        if not data.get("key"):
            raise IRError("node: обязателен key")
        node_type = data.get("type")
        if not isinstance(node_type, str) or (node_type not in NODE_TYPES and node_type not in BLOCK_NAMES):
            raise IRError(
                f"node {data['key']!r}: тип {node_type!r} отсутствует в каталоге блоков "
                f"(assets/block_index.json, {len(BLOCK_NAMES)} блоков); "
                f"для отсутствующего действия используйте ToDo"
            )
        physical = resolve_physical(node_type)
        if data.get("display_name") is not None and not isinstance(data["display_name"], str):
            raise IRError(f"node {data['key']}: display_name должен быть строкой")
        if data.get("unresolved_action") is not None and not isinstance(data["unresolved_action"], str):
            raise IRError(f"node {data['key']}: unresolved_action должен быть строкой")
        if physical == "ToDoBlock" and not data.get("unresolved_action"):
            raise IRError(f"ToDo {data['key']}: обязателен unresolved_action (confirmed-over-invented)")
        props = data.get("properties")
        if props is not None and not isinstance(props, dict):
            raise IRError(f"node {data['key']}: properties должен быть объектом")
        # тип значения проверяет валидатор (SHP-PROP-003), а не парсер IR:
        # так ошибка попадает в общий отчёт с кодом и профилем
        if physical == "Assign":
            assignments = data.get("assignments", [])
            if not isinstance(assignments, list):
                raise IRError(f"Assign {data['key']}: assignments должен быть списком")
            for a in assignments:
                if not isinstance(a, dict):
                    raise IRError(f"Assign {data['key']}: assignment должен быть объектом")
                _check_keys(a, frozenset({"target", "expression"}), f"Assign {data['key']} assignment")
                if not isinstance(a.get("target"), str):
                    raise IRError(f"Assign {data['key']}: target должен быть строкой")
                if not isinstance(a.get("expression"), Expr):
                    raise IRError(f"Assign {data['key']}: expression должен быть выражением")
        if physical in ("IfCondition", "While") and "condition" not in data:
            raise IRError(f"{node_type} {data['key']}: обязательно condition")
        if "condition" in data and not isinstance(data["condition"], Expr):
            raise IRError(f"node {data['key']}: condition должен быть выражением")
        if physical == "For":
            for field in ("start_index", "end_index", "step"):
                if data.get(field) is not None and not isinstance(data[field], Expr):
                    raise IRError(f"For {data['key']}: {field} должен быть выражением")
            if data.get("index_variable") is not None and not isinstance(data["index_variable"], str):
                raise IRError(f"For {data['key']}: index_variable должен быть строкой")
        if physical == "InvokeExpression":
            # единственный типизированный путь блока; raw-перекрытие Expression1
            # через properties запрещено (SHP-PROP-001)
            if "expression" not in data:
                raise IRError(f"InvokeExpression {data['key']}: обязательно expression")
            if not isinstance(data["expression"], Expr):
                raise IRError(f"node {data['key']}: expression должен быть выражением")
        super().__init__(data)


class Edge(dict):
    def __init__(self, data: dict):
        _check_keys(data, _EDGE_KEYS, "edge")
        if not (data.get("from") and data.get("to")):
            raise IRError("edge: обязательны from и to")
        super().__init__({"from_port": "Next", "to_port": "Input", **data})


class ProcessIR(dict):
    """Корень IR. parse() — строгая загрузка из dict/JSON-строки, dumps() — канонический JSON."""

    _keys = frozenset(
        {"process", "variables", "nodes", "edges", "external_variables", "unresolved_actions", "metadata"}
    )

    def __init__(self, data: dict):
        _check_keys(data, self._keys, "process IR")
        proc = data.get("process", {})
        _check_keys(proc, frozenset({"name", "mode"}), "process")
        if proc.get("mode", "draft") not in MODES:
            raise IRError(f"process.mode должен быть {sorted(MODES)}")
        raw_vars = data.get("variables", [])
        raw_nodes = data.get("nodes", [])
        raw_edges = data.get("edges", [])
        if not isinstance(raw_vars, list) or not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise IRError("IR: variables, nodes и edges должны быть списками")
        variables = [Variable(v) for v in raw_vars]
        nodes = [Node(n) for n in raw_nodes]
        edges = [Edge(e) for e in raw_edges]
        external = data.get("external_variables", [])
        if not isinstance(external, list):
            raise IRError("external_variables должен быть списком объектов {name, expected_type}")
        for e in external:
            if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"]:
                raise IRError("external_variables: каждый элемент — объект с непустым name")
            if e.get("expected_type") is not None and not isinstance(e["expected_type"], str):
                raise IRError("external_variables: expected_type должен быть строкой")
        self._check_graph(nodes, edges, variables)
        # unresolved_actions: ключ ещё принимается (старые IR), но поле устарело —
        # реестр ToDo генерируется из узлов при сборке (<out>.todo.json)
        super().__init__(
            {
                "process": {"name": proc.get("name", "Process"), "mode": proc.get("mode", "draft")},
                "variables": variables,
                "nodes": nodes,
                "edges": edges,
                "external_variables": list(data.get("external_variables", [])),
                "metadata": dict(data.get("metadata", {})),
            }
        )

    @staticmethod
    def _check_graph(nodes: list[Node], edges: list[Edge], variables: list[Variable]) -> None:
        node_keys = [n["key"] for n in nodes]
        dupes = {k for k in node_keys if node_keys.count(k) > 1}
        if dupes:
            raise IRError(f"дублирующиеся ключи узлов: {sorted(dupes)}")
        known = set(node_keys)
        for e in edges:
            if e["from"] not in known:
                raise IRError(f"edge: from ссылается на несуществующий узел {e['from']!r}")
            if e["to"] not in known:
                raise IRError(f"edge: to ссылается на несуществующий узел {e['to']!r}")
        var_names = [v["name"] for v in variables]
        dupes = {v for v in var_names if var_names.count(v) > 1}
        if dupes:
            raise IRError(f"дублирующиеся имена переменных: {sorted(dupes)}")
        for n in nodes:
            physical = resolve_physical(n["type"])
            if physical == "Assign":
                for a in n.get("assignments", []):
                    if a.get("target") not in var_names:
                        raise IRError(f"Assign {n['key']}: target {a.get('target')!r} не объявлен")
            if physical == "ToDoBlock" and not n.get("unresolved_action"):
                raise IRError(f"ToDo {n['key']}: обязателен unresolved_action (confirmed-over-invented)")

    @classmethod
    def parse(cls, data: object) -> "ProcessIR":
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise IRError("IR должен быть JSON-объектом")

        def parse_deep(obj: object) -> object:
            if isinstance(obj, dict):
                out = {k: parse_deep(v) for k, v in obj.items()}
                if "kind" in out and set(out) <= _EXPR_KEYS:
                    return Expr(out)
                return out
            if isinstance(obj, list):
                return [parse_deep(x) for x in obj]
            return obj

        return cls(parse_deep(data))

    def dumps(self) -> str:
        return json.dumps(self, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
