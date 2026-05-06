#!/usr/bin/env python3
"""
Perspicacité v2 - Web Interface with TRUE Agentic RAG

Features:
- LLM-driven orchestration (not fixed pipeline)
- Intent-based routing
- Session-scoped knowledge bases
- Conversation context
- Streaming responses with transparency
"""

import os
import sys
import json
import base64
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
from perspicacite.models.kb import KnowledgeBase, ChunkConfig, chroma_collection_name_for_kb
from perspicacite.models.rag import RAGMode

# Configure logging with file output.
# Must use explicit root-logger setup instead of basicConfig, because
# early imports (session_store → perspicacite.logging → structlog) can
# attach handlers before basicConfig runs, making it a silent no-op.
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"web_app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

_log_fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
_file_handler = logging.FileHandler(log_file)
_file_handler.setFormatter(_log_fmt)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_fmt)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(_file_handler)
_root.addHandler(_stream_handler)

logger = logging.getLogger("perspicacite.web")


# =============================================================================
# SECTION 2: Application State
# =============================================================================

from perspicacite.web.state import app_state



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    await app_state.initialize()
    yield
    # Cleanup
    logger.info("Shutting down...")


app = FastAPI(title="Perspicacité v2 - True Agentic RAG", lifespan=lifespan)

from perspicacite.web.routers import health as health_router
app.include_router(health_router.router)

from perspicacite.web.routers import conversations as conversations_router
app.include_router(conversations_router.router)

from perspicacite.web.routers import chat as chat_router
app.include_router(chat_router.router)

from perspicacite.web.routers import kb as kb_router
app.include_router(kb_router.router)

from perspicacite.web.routers import kb as kb_router
app.include_router(kb_router.router)

from perspicacite.web.routers import survey as survey_router
app.include_router(survey_router.router)


# =============================================================================
# SECTION 4: Web UI Route
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def get_chat_interface():
    """Serve the chat interface."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
    else:
        content = "<h1>Perspicacité v2</h1><p>Template not found. Please ensure templates/index.html exists.</p>"
    return HTMLResponse(content=content)


# =============================================================================
# SECTION 6: Health & System Routes
# =============================================================================

@app.get("/favicon.ico")
async def favicon():
    """Return empty favicon to prevent 404 errors in logs."""
    from fastapi.responses import Response
    return Response(content=b"", media_type="image/x-icon")



# =============================================================================
# SECTION 10: Main Entry Point
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
