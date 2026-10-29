"""Правила генерации 3, 4, 7 — машинная проверка вместо текста в SKILL.md.

Правило без проверки перестаёт различать случаи: эти тесты фиксируют, что
«не заполняй дефолтами», «ToDo = имя действия» и «не выдумывай значения»
подсвечиваются валидатором до сборки, и при этом не блокируют выпуск
(эвристика, а не запрет).
"""

from __future__ import annotations

import pytest

from sherpa_xml import compiler, ir, validator

TEMPLATES = compiler.Compiler().templates


def _check(nodes: list[dict], edges: list[dict] | None = None,
           variables: list[dict] | None = None,
           profile: str = "release") -> dict:
    data = {
        "process": {"name": "Gen", "mode": profile},
        "variables": variables or [],
        "nodes": nodes,
        "edges": edges or [],
    }
    return validator.validate_ir(ir.ProcessIR.parse(data), profile, templates=TEMPLATES)


def _chain(*middle: dict, variables: list[dict] | None = None, profile: str = "release") -> dict:
    nodes = [{"key": "s", "type": "Start"}, *middle, {"key": "z", "type": "Stop"}]
    edges = [{"from": "s", "from_port": "Next", "to": middle[0]["key"], "to_port": "Input"}]
    for prev, cur in zip(middle, middle[1:]):
        edges.append({"from": prev["key"], "from_port": "Next", "to": cur["key"], "to_port": "Input"})
    edges.append({"from": middle[-1]["key"], "from_port": "Next", "to": "z", "to_port": "Input"})
    return _check(nodes, edges, variables, profile)


def _codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def test_property_equal_to_template_default_is_warned():
    # Log.Value по умолчанию `""` — задавать его пустым бессмысленно
    report = _chain({"key": "l", "type": "Log", "properties": {"Value": '""'}})
    assert "SHP-GEN-001" in _codes(report)
    assert report["status"] == "passed"          # правило не блокирует выпуск


def test_property_with_real_value_is_not_warned():
    report = _chain({"key": "l", "type": "Log", "properties": {"Value": '"готово"'}})
    assert "SHP-GEN-001" not in _codes(report)


def test_empty_property_string_is_warned():
    report = _chain({"key": "l", "type": "Log", "properties": {"Level": ""}})
    assert "SHP-GEN-001" in _codes(report)


def test_todo_action_should_be_short_action_not_implementation():
    report = _chain({"key": "t", "type": "ToDo",
                     "unresolved_action": "Отправить письмо"}, profile="draft")
    assert "SHP-GEN-002" not in _codes(report)

    long_action = ("Через InvokeExpression выполнить регулярное выражение над "
                   "текстом и записать результат")
    report = _chain({"key": "t", "type": "ToDo", "unresolved_action": long_action},
                    profile="draft")
    assert "SHP-GEN-002" in _codes(report)


def test_invented_infrastructure_in_property_is_warned():
    report = _chain({"key": "f", "type": "FileRead",
                     "properties": {"FileName": '"\\\\\\\\srv01\\\\share\\\\task.txt"',
                                    "Result": "$Result"}})
    assert "SHP-GEN-003" in _codes(report)


def test_invented_domain_in_text_initial_is_warned():
    variables = [{"key": "v", "name": "Адрес", "sherpa_type": "Text",
                  "initial": {"kind": "string_literal", "value": "robot.company.local"}}]
    report = _chain({"key": "l", "type": "Log", "properties": {"Value": '"x"'}},
                    variables=variables)
    assert "SHP-GEN-003" in _codes(report)


def test_secret_literal_in_property_is_warned():
    report = _chain({"key": "f", "type": "FileRead",
                     "properties": {"FileName": '"Bearer abcdefghijklmnop1234"',
                                    "Result": "$Result"}})
    assert "SHP-GEN-004" in _codes(report)
    assert "SHP-GEN-003" not in _codes(report)   # секрет сообщается один раз, не как URL


def test_secret_value_is_not_echoed_in_report():
    # правило «секреты не перепечатывать»: в отчёт попадает класс, а не значение
    secret = "Bearer abcdefghijklmnop1234"
    report = _chain({"key": "f", "type": "FileRead",
                     "properties": {"FileName": f'"{secret}"', "Result": "$Result"}})
    message = next(i["message"] for i in report["issues"] if i["code"] == "SHP-GEN-004")
    assert "Bearer-токен" in message
    assert "abcdefghijklmnop" not in message


def test_nonempty_secure_string_is_error():
    # SEM-VAR-011: такой IR компилятор не соберёт — ошибка видна до сборки
    variables = [{"key": "p", "name": "Пароль", "sherpa_type": "SecureString",
                  "initial": {"kind": "string_literal", "value": "secret"}}]
    report = _chain({"key": "l", "type": "Log", "properties": {"Value": '"x"'}},
                    variables=variables)
    assert "SEM-VAR-011" in _codes(report)
    assert report["status"] == "failed"
    # пустой SecureString — норма
    variables[0]["initial"] = {"kind": "string_literal", "value": ""}
    report = _chain({"key": "l", "type": "Log", "properties": {"Value": '"x"'}},
                    variables=variables)
    assert "SEM-VAR-011" not in _codes(report)


def test_clean_generation_has_no_generation_warnings():
    variables = [{"key": "v", "name": "Файл", "sherpa_type": "Text",
                  "initial": {"kind": "string_literal", "value": ""}}]
    report = _chain({"key": "f", "type": "FileRead",
                     "properties": {"FileName": "$Файл", "Result": "$Результат"}},
                    variables=variables + [
                        {"key": "r", "name": "Результат", "sherpa_type": "Text",
                         "initial": {"kind": "string_literal", "value": ""}}])
    assert not {c for c in _codes(report) if c.startswith("SHP-GEN")}, report["issues"]
