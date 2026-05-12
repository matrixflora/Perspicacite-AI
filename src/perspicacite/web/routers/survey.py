"""Literature survey routes."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from perspicacite.web.state import app_state


logger = logging.getLogger(__name__)


router = APIRouter()


class SurveySelectionRequest(BaseModel):
    """Request to update paper selection for literature survey."""
    session_id: str
    selected_paper_ids: List[str]


class SurveyGenerateRequest(BaseModel):
    """Request to generate deep analysis for selected papers."""
    session_id: str


@router.get("/api/survey/{session_id}")
async def get_survey_session(session_id: str):
    """Get literature survey session status and papers."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    from perspicacite.models.rag import RAGMode

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


@router.post("/api/survey/{session_id}/select")
async def update_survey_selection(session_id: str, request: SurveySelectionRequest):
    """Update paper selection for literature survey."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    from perspicacite.models.rag import RAGMode

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


@router.post("/api/survey/{session_id}/generate")
async def generate_survey_report(session_id: str):
    """Generate deep analysis report for selected papers."""
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    from perspicacite.models.rag import RAGResponse
    from perspicacite.models.rag import RAGMode

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
