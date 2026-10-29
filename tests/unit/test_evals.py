"""Eval-пары регрессии: план → IR → build → сравнение с эталоном (числом).

Каждая пара — реальная задача с эталоном: артефакт Designer (`best-screenshots`),
замороженная release-сборка (`screenshots5`) и верхний уровень реального проекта
OCR (`ocr-skeleton`). Правка скила, уронившая структуру, роняет тест.
"""

from __future__ import annotations

import json

import pytest

from evals import EvalError, describe, load_spec, run_generate, specs

EVAL_DIRS = [path.parent for path in specs()]


def test_at_least_three_eval_pairs():
    assert len(EVAL_DIRS) >= 3, [d.name for d in EVAL_DIRS]


@pytest.mark.parametrize("eval_dir", EVAL_DIRS, ids=[d.name for d in EVAL_DIRS])
def test_eval_pair(eval_dir, tmp_path, capsys):
    result = run_generate(eval_dir, tmp_path)
    capsys.readouterr()                      # вывод build относится к сборке, не к eval
    failed = [c for c in result["checks"] if not c["ok"]]
    assert not failed, describe(result) + "\n" + json.dumps(result["comparison"], ensure_ascii=False)
    assert result["lint"]["P0"] == 0, f"{eval_dir.name}: артефакт содержит P0 (секреты/дефекты)"


@pytest.mark.parametrize("eval_dir", EVAL_DIRS, ids=[d.name for d in EVAL_DIRS])
def test_eval_fixture_is_complete(eval_dir):
    spec = load_spec(eval_dir)
    task = eval_dir / spec["task"]
    assert task.exists() and task.read_text(encoding="utf-8").strip(), "нужен текст задачи"
    assert (eval_dir / spec["plan"]).exists(), "нужен план (ответ на задачу)"
    reference = (eval_dir / spec["reference"]["path"]).resolve()
    assert reference.exists(), f"нет эталона {reference}"
    assert spec.get("note"), "у пары должно быть описание: что именно она защищает"


def test_eval_reports_wrong_threshold(tmp_path):
    # проверка, что eval действительно падает при недостижимом пороге
    from evals import EVALS
    eval_dir = EVALS / "screenshots5"
    spec = json.loads((eval_dir / "eval.json").read_text(encoding="utf-8"))
    spec["checks"] = [{"metric": "score", "min": 1.0001}]
    mutated = tmp_path / "screenshots5"
    mutated.mkdir()
    for name in ("plan.json", "task.md", "reference.process", "eval.json"):
        if (eval_dir / name).exists():
            (mutated / name).write_bytes((eval_dir / name).read_bytes())
    (mutated / "eval.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    result = run_generate(mutated, tmp_path / "work")
    assert not all(c["ok"] for c in result["checks"])


def test_run_generate_rejects_broken_reference(tmp_path):
    from evals import EVALS
    eval_dir = EVALS / "screenshots5"
    mutated = tmp_path / "broken"
    mutated.mkdir()
    for name in ("plan.json", "task.md", "eval.json"):
        (mutated / name).write_bytes((eval_dir / name).read_bytes())
    with pytest.raises(EvalError, match="эталон"):
        run_generate(mutated, tmp_path / "work")
