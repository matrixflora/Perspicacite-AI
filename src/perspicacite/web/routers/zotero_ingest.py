"""Zotero → KB ingest endpoints: plan + async build."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from perspicacite.integrations.zotero import ZoteroClient
from perspicacite.integrations.zotero_ingest import (
    ZoteroKBPlanEntry,
    build_kbs_from_zotero,
    plan_kbs_from_zotero,
)
from perspicacite.web.state import app_state

router = APIRouter(prefix="/api/zotero", tags=["zotero-ingest"])

# Hold strong refs to in-flight tasks (asyncio can GC them otherwise).
_ingest_tasks: set[asyncio.Task[Any]] = set()


class BuildKBsRequest(BaseModel):
    plan: list[ZoteroKBPlanEntry]


def _client_from_state() -> ZoteroClient:
    cfg = getattr(getattr(app_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.library_id):
        raise HTTPException(status_code=503, detail="Zotero not configured")
    base_url = getattr(cfg, "base_url", "") or None
    is_local = base_url and ("localhost" in base_url or "127.0.0.1" in base_url)
    if not cfg.api_key and not is_local:
        raise HTTPException(status_code=503, detail="Zotero api_key required for non-local base_url")
    return ZoteroClient(
        api_key=cfg.api_key,
        library_id=cfg.library_id,
        library_type=cfg.library_type,
        collection_key=cfg.collection_key,
        base_url=base_url,
    )


@router.get("/plan")
async def get_plan() -> dict[str, Any]:
    client = _client_from_state()
    plan = await plan_kbs_from_zotero(client, include_unfiled=True)
    return {"library_name": "Library", "plan": [p.model_dump() for p in plan]}


@router.post("/build-kbs/async")
async def build_kbs_async(payload: BuildKBsRequest) -> dict[str, Any]:
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    client = _client_from_state()
    job_id = await app_state.job_registry.create("zotero_ingest", total=len(payload.plan))
    task = asyncio.create_task(
        build_kbs_from_zotero(
            client,
            plan=payload.plan,
            app_state=app_state,
            registry=app_state.job_registry,
            job_id=job_id,
        )
    )
    _ingest_tasks.add(task)
    task.add_done_callback(_ingest_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
