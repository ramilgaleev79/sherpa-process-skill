#!/usr/bin/env python3
"""CLI скила sherpa-process.

Команды:
    init-ir FILE     — записать шаблонный IR (пример всех подтверждённых узлов)
    validate-ir FILE — проверить IR против закрытого контракта ir.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sherpa_xml import ir  # noqa: E402

TEMPLATE = {
    "process": {"name": "Example Process", "mode": "draft"},
    "variables": [
        {"key": "var_1", "name": "Counter", "sherpa_type": "Number", "scope": "local",
         "initial": {"kind": "number_literal", "value": 0}},
        {"key": "var_2", "name": "Enabled", "sherpa_type": "Logical", "scope": "local",
         "initial": {"kind": "boolean_literal", "value": True}},
        {"key": "var_3", "name": "Caption", "sherpa_type": "Text", "scope": "local",
         "initial": {"kind": "string_literal", "value": "Привет мир"}},
    ],
    "nodes": [
        {"key": "start_1", "type": "Start"},
        {"key": "assign_1", "type": "Assign", "assignments": [
            {"target": "Counter",
             "expression": {"kind": "arithmetic", "operator": "add",
                            "left": {"kind": "variable_reference", "variable": "Counter"},
                            "right": {"kind": "number_literal", "value": 1}}},
        ]},
        {"key": "cond_1", "type": "Condition",
         "condition": {"kind": "compare", "operator": "lt",
                       "left": {"kind": "variable_reference", "variable": "Counter"},
                       "right": {"kind": "number_literal", "value": 3}},
         "branches": {"yes": "stop_1", "no": "assign_1"}},
        {"key": "stop_1", "type": "Stop"},
    ],
    "edges": [
        {"from": "start_1", "to": "assign_1"},
        {"from": "assign_1", "to": "cond_1"},
        {"from": "cond_1", "from_port": "Yes", "to": "stop_1", "to_port": "Input"},
        {"from": "cond_1", "from_port": "No", "to": "assign_1", "to_port": "InputLeft"},
    ],
    "external_variables": [],
    "unresolved_actions": [],
    "metadata": {"designer_version": "1.4.4"},
}


def cmd_init_ir(path: Path) -> int:
    parsed = ir.ProcessIR.parse(TEMPLATE)  # шаблон сам должен проходить контракт
    path.write_text(parsed.dumps(), encoding="utf-8")
    print(f"OK: шаблонный IR записан в {path}")
    return 0


def cmd_validate_ir(path: Path) -> int:
    try:
        parsed = ir.ProcessIR.parse(path.read_text(encoding="utf-8"))
    except ir.IRError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — JSON-ошибки и пр.
        print(f"FAIL: {exc}")
        return 1
    n_nodes = len(parsed["nodes"])
    n_edges = len(parsed["edges"])
    print(f"OK: IR валиден — {len(parsed['variables'])} переменных, {n_nodes} узлов, {n_edges} связей")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init-ir", help="записать шаблонный IR")
    p_init.add_argument("file", type=Path)
    p_val = sub.add_parser("validate-ir", help="проверить IR")
    p_val.add_argument("file", type=Path)
    args = ap.parse_args()
    if args.cmd == "init-ir":
        return cmd_init_ir(args.file)
    return cmd_validate_ir(args.file)


if __name__ == "__main__":
    sys.exit(main())
