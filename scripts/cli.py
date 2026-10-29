#!/usr/bin/env python3
"""CLI скила sherpa-process.

Команды:
    init-ir FILE         — записать шаблонный IR (пример всех подтверждённых узлов)
    validate-ir FILE     — проверить IR против закрытого контракта ir.py
    build IR OUT [--mode draft|release|strict]
                         — скомпилировать .process (детерминированно):
                           валидация профиля → компиляция → пост-валидация XML
                           → детерминизм-чек; артефакты: OUT, OUT.report.json
                           (+ OUT.todo.json при наличии узлов ToDo)
    lint-process FILE…   — то же, что scripts/lint_process.py: ревью чужого
                           .process (дефекты P0/P1/P2). Отдельный скрипт — основной
                           вход (одна логика), подкоманда — для discoverability.
    flow-diagram FILE [--findings bugs.json] [--out O] [--check]
                         — HTML-схема .process; с findings — нумерованные баги
                           (красное где, зелёное как чинить), scripts/flow_diagram.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sherpa_xml import compiler, ir, validator  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402

TEMPLATE = {
    # Скелет без алгоритма: Start → один Assign → Stop.
    # Граф строится из текста задачи (references/graph-synthesis.md), а не копированием
    # готовых процессов. Ниже — только форма IR и перечень полей конструкций.
    "process": {"name": "New Process", "mode": "draft"},
    "variables": [
        {"key": "var_1", "name": "Счётчик", "sherpa_type": "Number", "scope": "local",
         "initial": {"kind": "number_literal", "value": 0}},
    ],
    "nodes": [
        {"key": "start_1", "type": "Start"},
        {"key": "assign_1", "type": "Assign", "assignments": [
            {"target": "Счётчик",
             "expression": {"kind": "number_literal", "value": 0}},
        ]},
        {"key": "stop_1", "type": "Stop"},
    ],
    "edges": [
        {"from": "start_1", "from_port": "Next", "to": "assign_1", "to_port": "Input"},
        {"from": "assign_1", "from_port": "Next", "to": "stop_1", "to_port": "Input"},
    ],
    "external_variables": [],
    "metadata": {
        "designer_version": "1.4.4",
        "hint": (
            "Это скелет IR, а не алгоритм. Сначала таблица шагов из задачи, затем конструкция "
            "по признаку (references/graph-synthesis.md). Полный синтаксис блоков — "
            "block_index.json / block-catalog.yaml."
        ),
        "constructs": {
            "Assign": "assignments: [{target, expression}] — до 10 пар",
            "Condition": "condition: {kind: compare|logical|variable_reference, ...}",
            "WhileLoop": "condition: {...}; выходы Iteration (тело) и Next",
            "ForLoop": "start_index, end_index, step, index_variable; выходы Index (тело) и Next",
            "TryCatch": "properties: {NumberAttempts, PauseBetweenAttempts}; выходы Next (тело), Out, Error",
            "ToDo": "unresolved_action: \"короткое действие из задачи\"",
            "произвольный блок": "type = InternalName из block_index.json; значения — в properties",
        },
        "variable_types": ["Number", "Logical", "Text", "SecureString", "Hashtable",
                           "Dictionary", "List", "DataTable", "Object", "DateTime", "Money"],
        "variable_type_notes": {
            "Dictionary": "SEM-VAR-010: объявление запрещено (контейнер не подтверждён); используйте Hashtable",
        },
    },
}

def _todo_registry(parsed: ir.ProcessIR) -> list[dict]:
    """Реестр нереализованных действий — из узлов ToDo (поле unresolved_actions в IR устарело)."""
    return [
        {"node": n["key"], "action": n["unresolved_action"],
         "display_name": n.get("display_name") or n["unresolved_action"]}
        for n in parsed["nodes"]
        if n["type"] == "ToDo"
    ]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_failed_report(out_path: Path, ir_path: Path, mode: str, issues: list[dict],
                         stage: str = "validate") -> None:
    """Отчёт о провальном выпуске: артефакт пишется всегда (validate/compile/determinism), файл — нет."""
    _write_json(Path(str(out_path) + ".report.json"), {
        "tool": "sherpa-process build",
        "ir": str(ir_path),
        "output": None,
        "mode": mode,
        "status": "failed",
        "stage": stage,
        "issues": issues,
    })


def cmd_init_ir(path: Path) -> int:
    parsed = ir.ProcessIR.parse(TEMPLATE)  # шаблон сам должен проходить контракт
    path.write_text(parsed.dumps(), encoding="utf-8")
    print(f"OK: шаблонный IR записан в {path}")
    return 0


def _load_templates() -> dict | None:
    """Шаблоны блоков для сверки properties при validate-ir (нет файла — проверки деградируют)."""
    try:
        return compiler.Compiler().templates
    except Exception:  # noqa: BLE001 — установленная копия без assets не должна ронять CLI
        return None


def cmd_validate_ir(path: Path, profile: str, report_path: Path | None) -> int:
    try:
        parsed = ir.ProcessIR.parse(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 — JSON/IRError
        print(f"FAIL: {exc}")
        return 1
    templates = _load_templates()
    if templates is None:
        print("FAIL: не удалось загрузить assets/block_templates.json — "
              "проверка свойств и портов невозможна (fail closed)")
        return 1
    try:
        report = validator.validate_ir(parsed, profile, templates=templates)
    except Exception as exc:  # noqa: BLE001 — не отдавать traceback вместо отчёта
        print(f"FAIL: валидатор упал: {type(exc).__name__}: {exc}")
        return 1
    report["todo_registry"] = _todo_registry(parsed)
    for issue in report["issues"]:
        print(f"  [{issue['severity']:5}] {issue['code']:14} {issue['location']}: {issue['message']}")
    blocking = [i for i in report["issues"] if i["severity"] in ("FATAL", "ERROR")]
    print(f"{'FAIL' if blocking else 'OK'}: {len(report['issues'])} замечаний, "
          f"блокирующих {len(blocking)} (profile={profile})")
    if report_path is not None:
        _write_json(report_path, report)
        print(f"отчёт: {report_path}")
    return 1 if report["status"] != "passed" else 0


def cmd_build(ir_path: Path, out_path: Path, mode: str) -> int:
    try:
        parsed = ir.ProcessIR.parse(ir_path.read_text(encoding="utf-8-sig"))
        parsed["process"]["mode"] = mode
        comp = compiler.Compiler()
        report_ir = validator.validate_ir(parsed, profile=mode, templates=comp.templates)
        if report_ir["status"] != "passed":
            for issue in report_ir["issues"]:
                if issue["severity"] in ("FATAL", "ERROR"):
                    print(f"  [{issue['severity']:5}] {issue['code']:14} {issue['location']}: {issue['message']}")
            print(f"FAIL: валидация профиля {mode} не пройдена — файл не выпущен")
            _write_failed_report(out_path, ir_path, mode, report_ir["issues"])
            return 1
        result = comp.compile(parsed)
        if comp.compile(parsed) != result:  # детерминизм: байт-в-байт
            print("FAIL: компиляция недетерминирована")
            _write_failed_report(out_path, ir_path, mode, [
                {"code": "SHP-DET-001", "severity": "ERROR", "location": "build",
                 "message": "повторная компиляция дала другой результат", "rule": "nondeterministic"}],
                stage="determinism")
            return 1
        payload = b"\xef\xbb\xbf" + result.encode("utf-8")
        # последний рубеж: пост-валидация сгенерированного XML
        report_xml = validator.validate_process_bytes(payload)
        if report_xml["status"] != "passed":
            for issue in report_xml["issues"]:
                print(f"  [{issue['severity']:5}] {issue['code']:14} {issue['location']}: {issue['message']}")
            print("FAIL: пост-валидация XML не пройдена — файл не выпущен")
            _write_failed_report(out_path, ir_path, mode, report_ir["issues"] + report_xml["issues"])
            return 1
        out_path.write_bytes(payload)
        todos = _todo_registry(parsed)
        report = {
            "tool": "sherpa-process build",
            "ir": str(ir_path),
            "output": str(out_path),
            "mode": mode,
            "status": "passed",
            "deterministic": True,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "ir_issues": report_ir["issues"],
            "process_issues": report_xml["issues"],
            "todo_registry": todos,
        }
        report_path = Path(str(out_path) + ".report.json")
        _write_json(report_path, report)
        tail = f", отчёт: {report_path}"
        if todos:
            todo_path = Path(str(out_path) + ".todo.json")
            _write_json(todo_path, todos)
            tail += f", реестр ToDo: {todo_path}"
        print(f"OK: {out_path} ({len(payload)} байт, mode={mode}, "
              f"warnings={len(report['ir_issues']) + len(report['process_issues'])}{tail})")
        return 0
    except Exception as exc:  # noqa: BLE001 — отчёт должен быть при любом провале
        print(f"FAIL: {type(exc).__name__}: {exc}")
        _write_failed_report(out_path, ir_path, mode, [
            {"code": "SHP-CMP-001", "severity": "ERROR", "location": "compile",
             "message": str(exc), "rule": "compile_error"}],
            stage="compile" if isinstance(exc, compiler.CompileError) else "parse_ir")
        return 1


def cmd_lint_process(files: list[Path], as_json: bool, fail_on: str, limit: int) -> int:
    """Ревью чужого .process — тонкая делегация в scripts/lint_process.py."""
    try:
        import lint_process        # noqa: PLC0415 — импорт по требованию: ревью не нужно build-пути
    except Exception as exc:  # noqa: BLE001 — установленная копия без линтера
        print(f"FAIL: линтер недоступен: {type(exc).__name__}: {exc}")
        return 1
    argv = [str(f) for f in files] + ["--fail-on", fail_on, "--limit", str(limit)]
    if as_json:
        argv.append("--json")
    return lint_process.main(argv)


def cmd_flow_diagram(process: Path, findings: Path | None, out: Path | None,
                     title: str | None, check: bool) -> int:
    """Блок-схема с багами — тонкая делегация в scripts/flow_diagram.py."""
    try:
        import flow_diagram          # noqa: PLC0415 — импорт по требованию
    except Exception as exc:  # noqa: BLE001 — установленная копия без рендерера
        print(f"FAIL: рендерер недоступен: {type(exc).__name__}: {exc}")
        return 1
    argv = [str(process)]
    if findings:
        argv += ["--findings", str(findings)]
    if out:
        argv += ["--out", str(out)]
    if title:
        argv += ["--title", title]
    if check:
        argv.append("--check")
    return flow_diagram.main(argv)


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init-ir", help="записать шаблонный IR")
    p_init.add_argument("file", type=Path)
    p_val = sub.add_parser("validate-ir", help="проверить IR")
    p_val.add_argument("file", type=Path)
    p_val.add_argument("--profile", choices=["draft", "release", "strict"], default="draft")
    p_val.add_argument("--report", type=Path, default=None, help="машинный отчёт JSON")
    p_build = sub.add_parser("build", help="скомпилировать .process")
    p_build.add_argument("ir_file", type=Path)
    p_build.add_argument("out_file", type=Path)
    p_build.add_argument("--mode", choices=["draft", "release", "strict"], default="draft")
    p_lint = sub.add_parser("lint-process", help="ревью чужого .process (дефекты P0/P1/P2)")
    p_lint.add_argument("files", nargs="+", type=Path)
    p_lint.add_argument("--json", action="store_true", help="машинный отчёт")
    p_lint.add_argument("--fail-on", choices=["p0", "p1", "none"], default="p0")
    p_lint.add_argument("--limit", type=int, default=12)
    p_flow = sub.add_parser("flow-diagram",
                            help="HTML-схема .process; с --findings — нумерованные "
                                 "баги: красное где, зелёное как чинить")
    p_flow.add_argument("process", type=Path)
    p_flow.add_argument("--findings", type=Path, default=None,
                        help="JSON с багами (references/flow-diagram.md)")
    p_flow.add_argument("--out", type=Path, default=None)
    p_flow.add_argument("--title", default=None)
    p_flow.add_argument("--check", action="store_true",
                        help="сверить два рендера байт-в-байт")
    args = ap.parse_args()
    if args.cmd == "init-ir":
        return cmd_init_ir(args.file)
    if args.cmd == "build":
        return cmd_build(args.ir_file, args.out_file, args.mode)
    if args.cmd == "lint-process":
        return cmd_lint_process(args.files, args.json, args.fail_on, args.limit)
    if args.cmd == "flow-diagram":
        return cmd_flow_diagram(args.process, args.findings, args.out,
                                args.title, args.check)
    return cmd_validate_ir(args.file, args.profile, args.report)


if __name__ == "__main__":
    sys.exit(main())
