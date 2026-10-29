# -*- coding: utf-8 -*-
"""designer_refactor: семантика переименования переменных «как Designer».

Правила сняты с IL DiagramDesigner.exe 1.4.4.40199 (ev_362fbcd2…,
ClassRefactoringExpression125::Processed): построчно, вне кавычек `"`/`'`,
директивы #stoprefactoring/#startrefactoring. Плюс три пространства
значений (raw / XAML / файл) и интеграция со спецификацией правки.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sherpa_xml.designer_refactor import (  # noqa: E402
    QUOTE_FILE, QUOTE_RAW, QUOTE_XAML, directives_unbalanced, normalize_var_name,
    plan_rename, rename_in_text,
)


# --- normalize ---------------------------------------------------------------

@pytest.mark.parametrize("raw,ok", [
    ("$a", True), ("a", True), ("${имя}", True), ("$Переменная3", True),
    ("", False), ("$5x", False), ("${x", False), ("имя с пробелом", False),
    ("$x-y", False),
])
def test_normalize_var_name(raw, ok):
    if ok:
        assert normalize_var_name(raw) == raw.strip("${}$")
    else:
        with pytest.raises(ValueError):
            normalize_var_name(raw)


# --- raw-пространство: основная семантика ------------------------------------

def test_rename_outside_quotes_only():
    src = '$a = $b + 1\nWrite-Host "$a в строке $b"\n$l = \'литерал $a\''
    out = rename_in_text(src, {"a": "alpha", "b": "beta"})
    assert out == '$alpha = $beta + 1\nWrite-Host "$a в строке $b"\n$l = \'литерал $a\''


def test_rename_token_boundaries():
    src = "$ab $abc ${ab} $ab:x $5x $$ $("
    out = rename_in_text(src, {"ab": "X"})
    assert out == "$X $abc ${X} $ab:x $5x $$ $("


def test_braced_form_stays_braced():
    # ${ab}2 обязана остаться фигурной: $X2 — уже другая переменная
    assert rename_in_text("${ab}2", {"ab": "X"}) == "${X}2"
    assert rename_in_text("$ab2", {"ab": "X"}) == "$ab2"


def test_scope_qualifier_not_renamed():
    src = "$env:PATH; $a; $env:а"
    out = rename_in_text(src, {"a": "X", "env": "Y"})
    assert out == "$env:PATH; $X; $env:а"


def test_directives_toggle():
    src = ("$a = 1\n"
           "#StopRefactoring\n"
           "  $a = 2\n"
           "#STARTREFACTORING\n"
           "$a = 3")
    out = rename_in_text(src, {"a": "X"})
    assert out == ("$X = 1\n"
                   "#StopRefactoring\n"
                   "  $a = 2\n"
                   "#STARTREFACTORING\n"
                   "$X = 3")


def test_directive_requires_exact_body():
    # внутренние пробелы значимы; хвост после директивы — уже не директива
    src = "#stoprefactoring $a\n$a = 1"
    assert rename_in_text(src, {"a": "X"}) == "#stoprefactoring $X\n$X = 1"


def test_crlf_preserved():
    src = "$a = 1\r\n$a = 2\r\n"
    assert rename_in_text(src, {"a": "X"}) == "$X = 1\r\n$X = 2\r\n"


def test_no_mapping_noop():
    assert rename_in_text("$a", {}) == "$a"


# --- наблюдения (plan) --------------------------------------------------------

def test_plan_kinds():
    src = '$a = 1\n"$a"\n#stoprefactoring\n$a\n'
    kinds = [f["kind"] for f in plan_rename(src, {"a": "X"})]
    assert kinds == ["replace", "in_quotes", "directive_zone"]


# --- пространства XAML/файл ---------------------------------------------------

def test_quote_xaml_space():
    src = '$a + &quot;$a&quot;'
    assert rename_in_text(src, {"a": "X"}, quote_mark=QUOTE_XAML) == "$X + &quot;$a&quot;"


def test_quote_file_space():
    # как в реальном .process: Value="&amp;quot;…$a…&amp;quot; + $a"
    src = 'Value="&amp;quot;текст $a&amp;quot; + $a'
    out = rename_in_text(src, {"a": "X"}, quote_mark=QUOTE_FILE)
    assert out == 'Value="&amp;quot;текст $a&amp;quot; + $X'


def test_directives_survive_escaping():
    src = "#stoprefactoring\r\n$a\r\n#startrefactoring\r\n$a"
    out = rename_in_text(src, {"a": "X"}, quote_mark=QUOTE_FILE)
    assert "$X" in out.split("#startrefactoring")[-1]
    assert "$a" in out.split("#stoprefactoring")[1]


# --- баланс директив ----------------------------------------------------------

def test_directives_unbalanced():
    assert directives_unbalanced("#stoprefactoring\n$x")
    assert not directives_unbalanced("#stoprefactoring\n$x\n#startrefactoring\n$y")
    assert not directives_unbalanced("$x")


# --- интеграция со спецификацией ----------------------------------------------

def _model(blocks: dict, variables: dict) -> dict:
    """Мини-модель в формате process_diff.load (diff читает raw/attrs)."""
    return {
        "blocks": {bid: {
            "internal": "Assign", "display": "d", "version": "1",
            "attrs": {}, "props": props, "raw": repr(sorted(props.items())),
        } for bid, props in blocks.items()},
        "edges": [],
        "variables": variables,
        "description": "",
        "sections": {},
    }


def test_spec_validation_rename():
    from edit_spec import SpecError, validate_spec
    blocks = {"b1": {"id": "b1", "internal": "Assign"}}
    validate_spec({"rename_variables": [{"from": "$a", "to": "b"}]}, blocks)
    with pytest.raises(SpecError):
        validate_spec({"rename_variables": [{"from": "a", "to": "a"}]}, blocks)   # no-op
    with pytest.raises(SpecError):
        validate_spec({"rename_variables": [{"from": "a", "to": "b"},
                                            {"from": "b", "to": "c"}]}, blocks)  # цепочка
    with pytest.raises(SpecError):
        validate_spec({"rename_variables": [{"from": "a", "to": "b"},
                                            {"from": "x", "to": "b"}]}, blocks)  # дубль цели
    with pytest.raises(SpecError):
        validate_spec({"rename_variables": [{"from": "$5x", "to": "b"}]}, blocks)


def test_compare_to_spec_rename_proof():
    from edit_spec import compare_to_spec
    old_model = _model(
        {"b1": {"Variable1": "$a", "Expression1": "\"$a в строке\""},
         "b2": {"Variable1": "$a"}},
        {"a": {"type": "Number", "default": "0"}})
    new_model = _model(
        {"b1": {"Variable1": "$x", "Expression1": "\"$a в строке\""},
         "b2": {"Variable1": "$x"}},
        {"x": {"type": "Number", "default": "0"}})
    from process_diff import diff
    spec = {"rename_variables": [{"from": "a", "to": "x"}]}
    check = compare_to_spec(diff(old_model, new_model), spec, old_model=old_model)
    assert check["ok"], (check["missing"], check["unexpected"])


def test_compare_to_spec_catches_skipped_block():
    # блок, который рефакторинг обязан был изменить, не изменился → missing
    from edit_spec import compare_to_spec
    from process_diff import diff
    old_model = _model({"b1": {"V": "$a"}, "b2": {"V": "$a"}},
                       {"a": {"type": "Number"}})
    new_model = _model({"b1": {"V": "$x"}, "b2": {"V": "$a"}},
                       {"x": {"type": "Number"}})
    spec = {"rename_variables": [{"from": "a", "to": "x"}]}
    check = compare_to_spec(diff(old_model, new_model), spec, old_model=old_model)
    assert not check["ok"]
    assert any("не изменилось" in m for m in check["missing"])


def test_compare_to_spec_catches_extra_change():
    # изменение свойства, не сводимое к рефакторингу → unexpected
    from edit_spec import compare_to_spec
    from process_diff import diff
    old_model = _model({"b1": {"V": "$a", "W": "константа"}},
                       {"a": {"type": "Number"}})
    new_model = _model({"b1": {"V": "$x", "W": "другое"}},
                       {"x": {"type": "Number"}})
    spec = {"rename_variables": [{"from": "a", "to": "x"}]}
    check = compare_to_spec(diff(old_model, new_model), spec, old_model=old_model)
    assert not check["ok"]
    assert any("незаявленное" in u for u in check["unexpected"])


# --- интеграция: полный цикл правки на реальном golden-файле -------------------

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "real" / "diag03-assign-4vars.process"


def _run(cmd: list[str]) -> int:
    import subprocess
    return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=str(Path(__file__).resolve().parents[2]))


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden-файлы не входят в runtime-копию")
def test_e2e_rename_cycle(tmp_path):
    """change_impact → apply_spec → process_diff --expect на реальном файле."""
    import json
    f = tmp_path / "t.process"
    f.write_bytes(GOLDEN.read_bytes())
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(
        {"rename_variables": [{"from": "$Переменная3", "to": "BodyText"}]},
        ensure_ascii=False), encoding="utf-8")
    env = [sys.executable]
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    assert _run(env + [str(scripts / "change_impact.py"), str(f),
                       "--spec", str(spec)]) == 0
    assert _run(env + [str(scripts / "apply_spec.py"), str(f),
                       "--spec", str(spec), "--backup"]) == 0
    assert _run(env + [str(scripts / "process_diff.py"),
                       str(tmp_path / "t.process.bak"), str(f),
                       "--expect", str(spec)]) == 0


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden-файлы не входят в runtime-копию")
def test_e2e_rename_with_set_props_override(tmp_path):
    """Документированное комбинирование: set_props на том же свойстве
    перекрывает рефакторинг — итоговое значение из set_props, rename по
    остальным блокам проходит, доказательство диффа сходится."""
    import json
    from edit_spec import compare_to_spec
    from process_diff import diff, load
    f = tmp_path / "t.process"
    f.write_bytes(GOLDEN.read_bytes())
    # найти блок, где Variable1 = $Переменная3, и перекрыть его вручную
    model = load(f)
    target = next(b for b, blk in model["blocks"].items()
                  if blk["props"].get("Variable1") == "$Переменная3")
    spec = {
        "rename_variables": [{"from": "$Переменная3", "to": "BodyText"}],
        "set_props": [{"block": target, "name": "Variable1",
                       "value": "$BodyText  # явное значение"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    env = [sys.executable]
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    assert _run(env + [str(scripts / "apply_spec.py"), str(f),
                       "--spec", str(spec_path), "--backup"]) == 0
    check = compare_to_spec(diff(model, load(f)), spec, old_model=model)
    assert check["ok"], (check["missing"], check["unexpected"])
    # перекрытое свойство получило ровно значение из set_props
    assert load(f)["blocks"][target]["props"]["Variable1"] == "$BodyText  # явное значение"
