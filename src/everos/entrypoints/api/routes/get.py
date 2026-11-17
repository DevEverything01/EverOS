"""POST /api/v2/memory/get — paginated listing endpoint.

Thin adapter: validate the request DTO, dispatch to the service layer,
return the envelope verbatim. ``request_id`` is generated inside the
service layer; we trust the value on the way out.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from everos.entrypoints.api.dependencies import ApplicationDep, require_read
from everos.service import GetRequest, GetResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/get", response_model=GetResponse, dependencies=[Depends(require_read)])
async def post_get(req: GetRequest, app: ApplicationDep) -> GetResponse:
    """Paginated listing over the requested ``memory_type``."""
    return await app.memory.get(req)
