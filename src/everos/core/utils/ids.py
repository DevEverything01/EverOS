"""Path-safe identifier alphabet and the episode memory-id grammar.

Ids that end up as directory or file-name segments under the memory root
(owner ids, scope ids, the owner part of an episode memory id) share one
character set: ``[a-zA-Z0-9_.@+-]``. It admits ``.`` (``user.name``) but no
path separator, and :func:`is_path_safe_segment` additionally refuses the
reserved ``.`` / ``..`` segments, so such an id can never leave its parent
directory.

An episode memory id is ``<owner_id>_<entry_id>`` with ``entry_id`` =
``ep_YYYYMMDD_<seq>``; the owner may itself contain underscores, so the split
is anchored at the entry id. The HTTP schema of ``/memory/delete_by_ids`` and
the service-side parser of ``/memory/update`` both validate with
:data:`EPISODE_MEMORY_ID`, so the two cannot drift apart.
"""

from __future__ import annotations

__all__ = [
    "EPISODE_MEMORY_ID",
    "PATH_SAFE_CHARS",
    "PATH_SAFE_CHARSET",
    "is_path_safe_segment",
]

import re
from typing import Final

PATH_SAFE_CHARS: Final = r"a-zA-Z0-9_.@+-"
"""The character class body (no brackets) of a path-safe id."""

PATH_SAFE_CHARSET: Final = rf"^[{PATH_SAFE_CHARS}]+$"
"""Anchored pattern of a whole path-safe id (pydantic ``pattern=`` ready)."""

EPISODE_MEMORY_ID: Final = re.compile(
    rf"(?P<owner>[{PATH_SAFE_CHARS}]{{1,128}})_(?P<entry>ep_(?P<day>\d{{8}})_\d+)"
)
"""``<owner>_ep_<YYYYMMDD>_<seq>``; use with ``fullmatch``."""

_PATH_SAFE_RE: Final = re.compile(PATH_SAFE_CHARSET)
_RESERVED_SEGMENTS: Final = frozenset({".", ".."})


def is_path_safe_segment(value: str) -> bool:
    """Whether ``value`` is a single path segment drawn from the safe set."""
    return value not in _RESERVED_SEGMENTS and _PATH_SAFE_RE.match(value) is not None
