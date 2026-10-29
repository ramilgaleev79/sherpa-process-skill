"""Профили валидации (runtime-источник истины — pure stdlib).

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
# strict СТРУКТУРНО надмножество release: выпускной профиль не может пропустить
# ошибку, блокируемую более мягким (тест test_strict_is_superset_of_release).
_RELEASE_FORBIDDEN = frozenset(
    {
        "unresolved_todo",              # SHP-TODO-001
        "variable_undeclared",          # SEM-REF-001 в release — ERROR
        "implicit_truthiness",          # SEM-EXPR-010 в release — ERROR
        "unconnected_branch",           # SHP-GRF-020: ветки циклов/условий/контейнеров
        "forloop_index_type",           # SEM-TYPE-002: переменная цикла For — Number
        "for_bounds",                   # SHP-GRF-021: For без end_index / step=0
    }
)
_STRICT_ONLY = frozenset(
    {
        "untyped_expression_in_condition",  # SEM-EXPR-011
        "freeform_powershell",              # SEM-EXPR-012 (в т.ч. вложенные операнды)
        "ambiguous_type_coercion",          # SEM-TYPE-010
        "invoke_expression",                # SEM-EXPR-013: блок произвольного PS запрещён
        "raw_property_grammar",             # SEM-PROP-002: raw-свойство — грамматика по @Type
    }
)

FORBIDDEN: dict[str, frozenset[str]] = {
    "draft": frozenset(),
    "release": _RELEASE_FORBIDDEN,
    "strict": _RELEASE_FORBIDDEN | _STRICT_ONLY,
}

# коды, которые в draft остаются предупреждениями
WARN_ONLY_IN_DRAFT = frozenset({"variable_undeclared", "implicit_truthiness"})

PROFILE_NAMES = ("draft", "release", "strict")


def blocking_codes(profile: str) -> frozenset[str]:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"профиль {profile!r}; допустимо {PROFILE_NAMES}")
    return FORBIDDEN[profile]
