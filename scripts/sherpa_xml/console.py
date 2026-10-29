"""UTF-8 stdio для CLI-инструментов скилла.

Windows-консоль по умолчанию использует cp1251/cp866: символы `→`, `…` и т.п.
дают UnicodeEncodeError. Переключаем потоки на UTF-8; при перенаправлении
вывода (GUI/pipe) это безопасно, а на старой консоли символы могут отобразиться
как «кракозябры», но команда не падает.
"""

from __future__ import annotations

import sys


def utf8_stdio() -> None:
    """Переключить stdout/stderr на UTF-8, если поток это поддерживает."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, TypeError):
            pass
