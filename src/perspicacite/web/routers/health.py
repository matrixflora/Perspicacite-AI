"""Health check route."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from perspicacite.web.state import app_state

router = APIRouter()


@router.get("/api/health")
async def health_check():
    """Health check endpoint."""
    provider_info = {}
    if app_state.initialized and app_state.config:
        llm_config = app_state.config.llm
        provider_info = {
            "default_provider": llm_config.default_provider,
            "default_model": llm_config.default_model,
            "available_providers": list(llm_config.providers.keys()),
        }

    return {
        "status": "healthy" if app_state.initialized else "initializing",
        "initialized": app_state.initialized,
        "timestamp": datetime.now().isoformat(),
        "llm": provider_info,
    }


@router.get("/api/databases/custom")
async def list_custom_databases() -> dict[str, list[dict]]:
    """Return user-defined databases from config.custom_databases.

    The frontend merges this list with its built-in DATABASES so custom
    entries appear in the composer's DB picker. Favicon is fetched
    client-side from the homepage domain.
    """
    if not app_state.initialized or app_state.config is None:
        return {"databases": []}
    customs = getattr(app_state.config, "custom_databases", None) or []
    return {
        "databases": [
            {
                "id": db.id,
                "label": db.label,
                "short": db.short or db.label[:2].upper(),
                "homepage": db.homepage,
                "blurb": db.blurb,
            }
            for db in customs
        ],
    }
