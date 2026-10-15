"""Профили валидации (main.md §4/§23; runtime-источник истины — pure stdlib).

Справочник для человека: references/validation-profiles.yaml (синхронность
проверяется тестом в dev-venv).

Семантика:
- FATAL/ERROR блокируют всегда (кроме WarnOnly в draft);
- release добавляет forbidden-коды (ToDo, неподтверждённые блоки, …);
- strict добавляет типовую строгость выражений.
"""

from __future__ import annotations

# Коды, которые профиль превращает из WARN в блокирующие
BLOCKING_SEVERITIES = {"FATAL", "ERROR"}

# forbidden-коды по профилю (правила их эмитят как ERROR только если код в списке)
FORBIDDEN: dict[str, frozenset[str]] = {
    "draft": frozenset(),
    "release": frozenset(
        {
            "unresolved_todo",              # SHP-TODO-001
            "unconfirmed_collection_block", # SHP-BLK-010 (Hashtable* без golden)
            "variable_undeclared",          # SEM-REF-001 в release — ERROR
            "implicit_truthiness",          # SEM-EXPR-010 в release — ERROR
        }
    ),
    "strict": frozenset(
        {
            "unresolved_todo",
            "unconfirmed_collection_block",
            "variable_undeclared",
            "implicit_truthiness",
            "untyped_expression_in_condition",  # SEM-EXPR-011
            "freeform_powershell",              # SEM-EXPR-012
            "ambiguous_type_coercion",          # SEM-TYPE-010
        }
    ),
}

# коды, которые в draft остаются предупреждениями
WARN_ONLY_IN_DRAFT = frozenset({"variable_undeclared", "implicit_truthiness"})

PROFILE_NAMES = ("draft", "release", "strict")


def blocking_codes(profile: str) -> frozenset[str]:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"профиль {profile!r}; допустимо {PROFILE_NAMES}")
    return FORBIDDEN[profile]
