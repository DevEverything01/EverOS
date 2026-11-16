"""Control-plane module placeholder.

The control plane lives in ``control.db`` (separate from the memory-domain
``system.db``) and provides authentication, audit logging and usage tracking
for the HTTP API.

Models are in ``infra.persistence.sqlite._tables.control``.
Store is ``infra.persistence.sqlite.ControlStore``.
Both are accessed through the ``Application.control`` field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only: the concrete adapter lives in infra
    from everos.infra.persistence.sqlite.control_store import ControlStore

__all__: list[str] = ["ControlStore"]
