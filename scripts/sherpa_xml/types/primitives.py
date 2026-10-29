"""Скалярные типы в декларации DiagramProperty (golden: b05, diag03).

Формы различаются по контексту: декларация vs выражение (§10.2 Logical).
"""

from __future__ import annotations


def number(value: int | float) -> str:
    """Number в декларации — плоская строка; целые без точки."""
    if isinstance(value, bool):
        raise ValueError("bool не является Number")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


def logical_declaration(value: bool) -> str:
    """Logical в декларации: True/False."""
    return "True" if value else "False"


def logical_expression(value: bool) -> str:
    """Logical в выражениях: $True/$False — отличается от декларации (§10.2)."""
    return "$True" if value else "$False"


def secure_string_empty() -> str:
    """Пустой SecureString в декларации — пустая строка (§10.4)."""
    return ""
