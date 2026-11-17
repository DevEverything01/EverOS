"""Ops write routes need a scope: quiesce ``admin``, OME trigger ``memory:write``.

Before: neither route had a scope dependency, so a ``memory:read`` key could
stop the index worker for the life of the process, or force an OME strategy
run (memory writes plus LLM spend).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .conftest import SecuredApp

QUIESCE = "/api/v2/cascade/quiesce"
TRIGGER = "/api/v2/ome/trigger"


@pytest.fixture
def ops(secured: SecuredApp) -> SimpleNamespace:
    ops = secured.app.state.application.ops
    ops.cascade_quiesce.return_value = SimpleNamespace(
        drained=True, pending_before=3, pending_after=0, failed_permanent=0
    )
    ops.ome_trigger.return_value = SimpleNamespace(status="ok", dispatched=1, runs=[])
    return ops


async def _post(secured: SecuredApp, key: str, path: str, body: dict | None):
    async with secured.client(headers={"X-Api-Key": key}) as client:
        return await client.post(path, json=body)


@pytest.mark.parametrize("key", ["read_key", "write_key"])
async def test_quiesce_refuses_non_admin(
    secured: SecuredApp, ops: SimpleNamespace, key: str
) -> None:
    response = await _post(secured, getattr(secured, key), QUIESCE, None)

    assert response.status_code == 403
    ops.cascade_quiesce.assert_not_awaited()


async def test_quiesce_admin_is_200(secured: SecuredApp, ops: SimpleNamespace) -> None:
    response = await _post(secured, secured.admin_key, QUIESCE, None)

    assert response.status_code == 200
    assert response.json()["quiesced"] is True
    ops.cascade_quiesce.assert_awaited_once()


async def test_ome_trigger_refuses_read_key(
    secured: SecuredApp, ops: SimpleNamespace
) -> None:
    response = await _post(secured, secured.read_key, TRIGGER, {"name": "s"})

    assert response.status_code == 403
    ops.ome_trigger.assert_not_awaited()


@pytest.mark.parametrize("key", ["write_key", "admin_key"])
async def test_ome_trigger_write_or_admin_is_200(
    secured: SecuredApp, ops: SimpleNamespace, key: str
) -> None:
    response = await _post(secured, getattr(secured, key), TRIGGER, {"name": "s"})

    assert response.status_code == 200
    assert response.json()["dispatched"] == 1
    ops.ome_trigger.assert_awaited_once()
