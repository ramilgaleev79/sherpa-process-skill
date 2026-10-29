"""Eval-пары: задача → план → IR → `.process` → сравнение с эталоном.

Цикл skill-creator проверяет правки скила числом, а не впечатлением: у каждой
пары есть текст задачи, план (ответ на неё), эталон и порог. Пайплайн тот же,
что у пользователя: `plan_to_ir` → `cli build` → `graph_compare`.

Раскладка пары:

    tests/fixtures/evals/<имя>/
        eval.json      — task, plan, profile, reference, checks (metric/min)
        task.md        — текст задачи, на который отвечает план
        plan.json      — таблица шагов (шаг = цитата из задачи)
        reference.*     — эталон: .process (диаграмма из Designer или
                           фиксированная release-сборка) либо .json-профиль графа

Эталон может быть профилем (`graph_compare.graph_profile`): так эталоном становится
крупный многослойный процесс, не копируя его мегабайты в репозиторий.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "dev"))

import cli  # noqa: E402
from dev.graph_compare import compare, load_graph  # noqa: E402
from dev.plan_to_ir import PlanCompiler  # noqa: E402
from lint_process import lint  # noqa: E402

EVALS = REPO / "tests" / "fixtures" / "evals"
METRICS = ("score", "type_score", "edge_score")


class EvalError(AssertionError):
    """Eval-пара не выполнилась: сборка не прошла или эталон не читается."""


def specs() -> list[Path]:
    """Все пары (eval.json), отсортированные по имени — порядок стабилен в pytest."""
    return sorted(EVALS.glob("*/eval.json"))


def load_spec(eval_dir: Path) -> dict:
    spec = json.loads((eval_dir / "eval.json").read_text(encoding="utf-8-sig"))
    for key in ("task", "plan", "reference", "checks"):
        if key not in spec:
            raise EvalError(f"{eval_dir.name}: в eval.json нет поля {key!r}")
    for check in spec["checks"]:
        if check.get("metric") not in METRICS:
            raise EvalError(f"{eval_dir.name}: метрика {check.get('metric')!r} вне {METRICS}")
    return spec


def run_generate(eval_dir: Path, workdir: Path) -> dict:
    """Пара генерации: план → IR → build → сравнение с эталоном + линт результата."""
    spec = load_spec(eval_dir)
    plan = json.loads((eval_dir / spec["plan"]).read_text(encoding="utf-8-sig"))
    ir_data = PlanCompiler(plan).compile()

    workdir.mkdir(parents=True, exist_ok=True)
    ir_path = workdir / "eval.ir.json"
    ir_path.write_text(json.dumps(ir_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    process = workdir / "eval.process"
    profile = spec.get("profile", "release")
    rc = cli.cmd_build(ir_path, process, profile)
    if rc != 0 or not process.exists():
        raise EvalError(f"{eval_dir.name}: сборка {profile} не прошла (rc={rc})")

    reference_path = (eval_dir / spec["reference"]["path"]).resolve()
    if not reference_path.exists():
        raise EvalError(f"{eval_dir.name}: нет эталона {reference_path}")
    comparison = compare(load_graph(reference_path), load_graph(process),
                         set(spec.get("ignore") or []))
    checks = [{"metric": c["metric"], "min": c["min"], "value": comparison[c["metric"]],
               "ok": comparison[c["metric"]] >= c["min"]} for c in spec["checks"]]
    lint_report = lint(process)
    return {
        "eval": eval_dir.name,
        "title": spec.get("title") or eval_dir.name,
        "profile": profile,
        "comparison": comparison,
        "checks": checks,
        "lint": lint_report["summary"],
        "process": process,
        "ir": ir_path,
        "note": spec.get("note") or "",
    }


def describe(result: dict) -> str:
    """Короткая строка для отчёта: <пара>: score=… (типы …, связи …)."""
    c = result["comparison"]
    failed = ", ".join(f"{x['metric']}={x['value']:.3f} < {x['min']}" for x in result["checks"]
                       if not x["ok"])
    return (f"{result['eval']}: score={c['score']:.3f} (типы {c['type_score']:.3f}, "
            f"связи {c['edge_score']:.3f})" + (f"; НЕ ПРОШЛО: {failed}" if failed else ""))
