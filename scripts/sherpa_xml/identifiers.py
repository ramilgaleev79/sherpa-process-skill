"""Детерминированные идентификаторы блоков (main.md §7).

UUIDv5 от стабильного namespace: одинаковый вход → одинаковый GUID.
Golden-идентификаторы никогда не копируются (§7.2).
"""

from __future__ import annotations

import uuid

# Фиксированный namespace скила: сгенерирован однажды, менять нельзя —
# детерминизм должен переживать релизы.
NAMESPACE_SHERPA = uuid.UUID("6fa7c2b1-58d4-4a8e-9f3b-2d1c0a9e7b55")


def block_id(process_name: str, node_key: str, block_type: str, stable_path: str = "") -> str:
    """GUID блока: uuid5(NAMESPACE, 'process::node_key::block_type[::stable_path]')."""
    seed = f"{process_name}::{node_key}::{block_type}"
    if stable_path:
        seed = f"{seed}::{stable_path}"
    return str(uuid.uuid5(NAMESPACE_SHERPA, seed))
