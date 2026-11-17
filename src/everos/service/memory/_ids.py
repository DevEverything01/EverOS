"""Episode memory ids: ``<owner_id>_<entry_id>``.

The owner id may itself contain underscores (``user_123``), so an id is split
at the entry id (``ep_YYYYMMDD_<seq>``) anchored to the end, never at the
first underscore. Shared by the delete and update use cases.

The owner is a path segment of the episode's daily file, so it must come from
the same safe alphabet the ``/memory/delete_by_ids`` schema enforces
(:mod:`everos.core.utils.ids`): no ``/``, ``\\``, absolute path or ``..``
segment can reach the store and resolve outside the memory root.
"""

from __future__ import annotations

__all__ = ["split_episode_memory_id"]

from everos.core.errors import InvalidMemoryIdError
from everos.core.utils.ids import EPISODE_MEMORY_ID, is_path_safe_segment


def split_episode_memory_id(memory_id: str) -> tuple[str, str]:
    """``<owner_id>_<entry_id>`` -> ``(owner_id, entry_id)``.

    Raises:
        InvalidMemoryIdError: ``memory_id`` is not an episode memory id, or
            its owner part is not a safe path segment (separators,
            ``.`` / ``..``, characters outside the safe set).
    """
    match = EPISODE_MEMORY_ID.fullmatch(memory_id)
    if match is None or not is_path_safe_segment(match["owner"]):
        raise InvalidMemoryIdError(f"not an episode memory id: {memory_id!r}")
    return match["owner"], match["entry"]
