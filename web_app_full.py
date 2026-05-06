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
# SECTION 9: Literature Survey API Endpoints
# =============================================================================

class SurveySelectionRequest(BaseModel):
    """Request to update paper selection for literature survey."""
    session_id: str
    selected_paper_ids: List[str]


class SurveyGenerateRequest(BaseModel):
    """Request to generate deep analysis for selected papers."""
    session_id: str


@app.get("/api/survey/{session_id}")
async def get_survey_session(session_id: str):
    """Get literature survey session status and papers."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    
    # Get the literature survey mode from RAGEngine
    survey_mode = None
    if app_state.rag_engine and RAGMode.LITERATURE_SURVEY in app_state.rag_engine._modes:
        survey_mode = app_state.rag_engine._modes[RAGMode.LITERATURE_SURVEY]
    
    if not survey_mode:
        return {"error": "Literature survey mode not available"}
    
    session = survey_mode.get_session(session_id)
    if not session:
        return {"error": "Session not found"}
    
    return {
        "session_id": session.session_id,
        "query": session.query,
        "papers_count": len(session.papers),
        "themes_count": len(session.themes),
        "selected_count": len(session.selected_papers),
        "themes": [
            {
                "name": t.name,
                "description": t.description,
                "paper_count": len(t.papers)
            }
            for t in session.themes
        ],
        "papers": [
            {
                "id": p.id,
                "title": p.title,
                "authors": p.authors,
                "year": p.year,
                "abstract": p.abstract[:300] + "..." if len(p.abstract) > 300 else p.abstract,
                "doi": p.doi,
                "citation_count": p.citation_count,
                "relevance_score": p.relevance_score,
                "themes": p.themes,
                "recommended": p.recommended,
                "reason": p.reason,
            }
            for p in session.papers
        ]
    }


@app.post("/api/survey/{session_id}/select")
async def update_survey_selection(session_id: str, request: SurveySelectionRequest):
    """Update paper selection for literature survey."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    
    survey_mode = None
    if app_state.rag_engine and RAGMode.LITERATURE_SURVEY in app_state.rag_engine._modes:
        survey_mode = app_state.rag_engine._modes[RAGMode.LITERATURE_SURVEY]
    
    if not survey_mode:
        return {"error": "Literature survey mode not available"}
    
    success = survey_mode.update_selection(session_id, request.selected_paper_ids)
    if not success:
        return {"error": "Failed to update selection"}
    
    return {
        "success": True,
        "session_id": session_id,
        "selected_count": len(request.selected_paper_ids)
    }


@app.post("/api/survey/{session_id}/generate")
async def generate_survey_report(session_id: str):
    """Generate deep analysis report for selected papers."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    from perspicacite.models.rag import RAGResponse
    
    survey_mode = None
    if app_state.rag_engine and RAGMode.LITERATURE_SURVEY in app_state.rag_engine._modes:
        survey_mode = app_state.rag_engine._modes[RAGMode.LITERATURE_SURVEY]
    
    if not survey_mode:
        return {"error": "Literature survey mode not available"}
    
    # Use LLM client from app_state
    llm = app_state.llm_client
    
    try:
        response = await survey_mode.generate_deep_analysis(session_id, llm)
        return {
            "success": True,
            "session_id": session_id,
            "answer": response.answer,
            "papers_analyzed": response.metadata.get("papers_analyzed", 0),
            "themes": response.metadata.get("themes", 0),
        }
    except Exception as e:
        logger.error(f"Failed to generate survey report: {e}")
        return {"error": f"Failed to generate report: {str(e)}"}



# =============================================================================
# SECTION 10: Main Entry Point
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
