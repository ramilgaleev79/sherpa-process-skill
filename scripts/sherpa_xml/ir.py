"""Process IR — промежуточное представление для генерации .process Sherpa RPA.

IR — единственное, что пишет агент (LLM); XML генерирует компилятор (фаза 4).
Контракт закрытый: неизвестные ключи/типы узлов/типы переменных — ошибка,
чтобы IR не расползался за пределы подтверждённого golden-набора.

Формат соответствует main.md §6/§19; детали сериализации — references/.
Pure stdlib (D1).
"""

from __future__ import annotations

import json

# --- Подтверждённые множества (golden/manifest.yaml, Designer 1.4.4) ---

VARIABLE_TYPES = frozenset(
    {"Number", "Logical", "Text", "SecureString", "Hashtable", "Dictionary", "List", "DataTable"}
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
    }
)

# Логические типы узлов IR → физические InternalName (абстракция §12.2 main.md).
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

COMPARE_OPS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "like", "match"})
LOGICAL_OPS = frozenset({"and", "or", "not", "xor"})
ARITHMETIC_OPS = frozenset({"add", "sub", "mul", "div", "mod", "pow"})

_EXPR_KEYS = frozenset({"kind", "variable", "value", "operator", "left", "right"})
_NODE_KEYS = frozenset(
    {
        "key",
        "type",
        "purpose",
        "unresolved_action",
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
        if kind in ("number_literal", "boolean_literal"):
            if not isinstance(self.get("value"), (int, float, bool)):
                raise IRError(f"{kind}: value должен быть числом/булевым")
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
        if data.get("type") not in NODE_TYPES:
            raise IRError(
                f"node {data['key']!r}: тип {data.get('type')!r} не подтверждён "
                f"(см. ir.NODE_TYPES); неподтверждённое действие оформляйте ToDo"
            )
        if data["type"] == "Assign":
            for a in data.get("assignments", []):
                _check_keys(a, frozenset({"target", "expression"}), f"Assign {data['key']} assignment")
                if "expression" not in a:
                    raise IRError(f"Assign {data['key']}: assignment без expression")
        if data["type"] in ("Condition", "WhileLoop") and "condition" not in data:
            raise IRError(f"{data['type']} {data['key']}: обязательно condition")
        if "condition" in data and not isinstance(data["condition"], Expr):
            raise IRError(f"node {data['key']}: condition должен быть выражением")
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
        variables = [Variable(v) for v in data.get("variables", [])]
        nodes = [Node(n) for n in data.get("nodes", [])]
        edges = [Edge(e) for e in data.get("edges", [])]
        self._check_graph(nodes, edges, variables)
        super().__init__(
            {
                "process": {"name": proc.get("name", "Process"), "mode": proc.get("mode", "draft")},
                "variables": variables,
                "nodes": nodes,
                "edges": edges,
                "external_variables": list(data.get("external_variables", [])),
                "unresolved_actions": list(data.get("unresolved_actions", [])),
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
            if n["type"] == "Assign":
                for a in n.get("assignments", []):
                    if a.get("target") not in var_names:
                        raise IRError(f"Assign {n['key']}: target {a.get('target')!r} не объявлен")
            if n["type"] == "ToDo" and not n.get("unresolved_action"):
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
