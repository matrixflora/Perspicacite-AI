"""Chat endpoint and streaming helpers.

Owns the /api/chat POST route and the three streaming generators that
produce SSE events for the agentic and RAG-mode flows.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from perspicacite.models.rag import RAGMode
from perspicacite.web.state import app_state


logger = logging.getLogger(__name__)

# Module-level map from mode string to RAGMode enum.
# Referenced by _stream_rag_mode and exposed for testing.
RAG_MODE_MAP = {
    "basic": RAGMode.BASIC,
    "advanced": RAGMode.ADVANCED,
    "profound": RAGMode.PROFOUND,
    "literature_survey": RAGMode.LITERATURE_SURVEY,
    "contradiction": RAGMode.CONTRADICTION,
}

router = APIRouter()


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: str = Field(..., description="user, assistant, or system")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request for chat endpoint - NOW WITH CONVERSATION SUPPORT."""

    query: str = Field(..., description="Current research question")
    messages: List[ChatMessage] = Field(default_factory=list, description="Conversation history")
    session_id: Optional[str] = Field(default=None, description="Session ID for persistence")
    conversation_id: Optional[str] = Field(
        default=None, description="Conversation ID for persistent chat thread"
    )
    kb_name: Optional[str] = Field(default=None, description="Knowledge base to search first")
    mode: str = Field(
        default="basic",
        description=(
            "RAG mode: basic, advanced, profound, agentic, literature_survey, contradiction"
        ),
    )
    stream: bool = Field(default=True, description="Stream the response")
    max_papers: int = Field(
        default=3, ge=1, le=10, description="Maximum papers to display in results"
    )
    max_papers_to_download: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum papers to download for full-text analysis (Agentic mode). Higher = more comprehensive but slower",
    )
    databases: List[str] = Field(
        default_factory=lambda: ["semantic_scholar", "openalex", "pubmed"],
        description="List of databases to search (semantic_scholar, openalex, pubmed, arxiv, ieee, springer, dblp)",
    )


class ChatResponse(BaseModel):
    """Response chunk for streaming."""

    type: str = Field(..., description="thinking, tool_call, tool_result, answer, error")
    content: Optional[str] = None
    message: Optional[str] = None
    step: Optional[str] = None
    tool: Optional[str] = None
    description: Optional[str] = None
    result_summary: Optional[str] = None
    details: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/api/chat")
async def chat_endpoint(request: ChatRequest, raw_request: Request):
    """
    Main chat endpoint with true agentic orchestration.

    Uses LLM-driven planning, not fixed workflow.
    """
    if not app_state.initialized:
        await app_state.initialize()

    # Get or create conversation for persistence
    conversation_id = request.conversation_id
    if app_state.session_store:
        if conversation_id:
            # Verify conversation exists
            conv = await app_state.session_store.get_conversation(conversation_id)
            if not conv:
                conversation_id = None  # Will create new below

        if not conversation_id:
            # Create new conversation
            session_id = request.session_id or str(uuid.uuid4())
            kb_name = request.kb_name or "default"
            # Use first 30 chars of query as title
            title = request.query[:30] + "..." if len(request.query) > 30 else request.query

            conv = await app_state.session_store.create_conversation(
                session_id=session_id,
                kb_name=kb_name,
                title=title,
            )
            conversation_id = conv.id
            logger.info(f"Created new conversation: {conversation_id} for session {session_id}")

    if request.stream:
        return StreamingResponse(
            agentic_chat_stream(request, conversation_id), media_type="text/event-stream"
        )
    else:
        # Non-streaming: consume the SSE stream internally, return JSON
        answer = ""
        sources = []
        papers_list = []
        answer_tokens = []
        error_message = None
        event_count = 0

        async for event in agentic_chat_stream(request, conversation_id):
            event_count += 1

            # Bug 4: Check for client disconnect every 5 events
            if event_count % 5 == 0:
                if await raw_request.is_disconnected():
                    logger.warning("client_disconnected_aborting_pipeline")
                    break

            if not event.startswith("data:"):
                continue
            try:
                data = json.loads(event[5:].strip())
            except json.JSONDecodeError:
                continue

            event_type = data.get("type", "")

            if event_type == "answer":
                content_b64 = data.get("content_b64")
                if content_b64:
                    answer = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                elif "content" in data:
                    answer = str(data["content"])

            elif event_type == "token":
                # Bug 2: Accumulate token deltas as fallback
                delta_b64 = data.get("delta_b64")
                if delta_b64:
                    try:
                        delta = base64.b64decode(delta_b64).decode("utf-8", errors="replace")
                        answer_tokens.append(delta)
                    except Exception:
                        pass
                elif "delta" in data:
                    answer_tokens.append(str(data["delta"]))

            elif event_type == "source":
                sources.append(data.get("source", {}))

            elif event_type == "papers_found":
                # Bug 1: Use "papers" key, not "count"
                papers_list = data.get("papers", [])

            elif event_type == "error":
                # Bug 3: Capture error instead of swallowing
                error_message = data.get("message", "Unknown pipeline error")
                logger.warning("non_streaming_pipeline_error", error=error_message)

        # Bug 2: If no "answer" event arrived, fall back to accumulated tokens
        if not answer and answer_tokens:
            answer = "".join(answer_tokens)

        # Bug 3: If pipeline errored with no answer, return HTTP 502
        if not answer and error_message:
            raise HTTPException(status_code=502, detail=error_message)

        return {
            "answer": answer,
            "sources": sources,
            "papers_found": len(papers_list) or len(sources),
            "conversation_id": conversation_id,
        }


async def agentic_chat_stream(request: ChatRequest, conversation_id: Optional[str] = None):
    """
    Stream chat responses using selected RAG mode.

    Routes to appropriate handler based on request.mode:
    - agentic: Uses AgenticOrchestrator (intent-based, tool use)
    - basic/advanced/profound: Uses RAGEngine with respective mode

    Yields SSE events with thinking steps, tool calls, and final answer.
    """
    from perspicacite.models.messages import Message

    # Save user message to conversation if we have one
    if conversation_id and app_state.session_store:
        try:
            await app_state.session_store.add_message(
                conversation_id, Message(role="user", content=request.query)
            )
        except Exception as e:
            logger.warning(f"Failed to save user message: {e}")

    assistant_content = ""

    try:
        logger.info(f"Chat request: {request.query[:50]}... | Mode: {request.mode}")

        # Route based on selected mode
        if request.mode == "agentic":
            # Use agentic orchestrator for full agentic behavior
            async for event in _stream_agentic(request, conversation_id):
                # Collect assistant content for saving
                if event.startswith("data:"):
                    try:
                        data = json.loads(event[5:].strip())
                        if data.get("type") == "answer":
                            content_b64 = data.get("content_b64")
                            if content_b64:
                                assistant_content = base64.b64decode(content_b64).decode("utf-8")
                    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError):
                        pass
                yield event
        else:
            # Use RAGEngine for other modes (basic, advanced, profound)
            async for event in _stream_rag_mode(request, conversation_id):
                # Collect assistant content for saving
                if event.startswith("data:"):
                    try:
                        data = json.loads(event[5:].strip())
                        if data.get("type") == "answer":
                            content_b64 = data.get("content_b64")
                            if content_b64:
                                assistant_content = base64.b64decode(content_b64).decode("utf-8")
                    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError):
                        pass
                yield event

    except Exception as e:
        logger.error(f"Error in chat stream: {e}", exc_info=True)
        error_data = json.dumps({"type": "error", "message": str(e)})
        yield f"data: {error_data}\n\n"

    # Save assistant message to conversation
    if conversation_id and app_state.session_store and assistant_content:
        try:
            await app_state.session_store.add_message(
                conversation_id, Message(role="assistant", content=assistant_content)
            )
            logger.info(f"Saved conversation messages to {conversation_id}")
        except Exception as e:
            logger.warning(f"Failed to save assistant message: {e}")


async def _stream_agentic(request: ChatRequest, conversation_id: Optional[str] = None):
    """Stream using AgenticOrchestrator."""
    async for event in app_state.orchestrator.chat(
        query=request.query,
        session_id=request.session_id,
        kb_name=request.kb_name,
        stream=True,
        max_papers_to_download=request.max_papers_to_download,
    ):
        # Large answer bodies as JSON strings are fragile over chunked HTTP (mid-string
        # splits → client JSON.parse "Unterminated string"). Ship answer text as base64.
        if event.get("type") == "answer":
            content = event.get("content") or ""
            safe = {
                "type": "answer",
                "session_id": event.get("session_id"),
                "conversation_id": conversation_id,
                "content_b64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
            data = json.dumps(safe, separators=(",", ":"))
        else:
            data = json.dumps(event, separators=(",", ":"))
        yield f"data: {data}\n\n"

    # End of stream
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _stream_rag_mode(request: ChatRequest, conversation_id: Optional[str] = None):
    """Stream using RAGEngine with selected mode (basic, advanced, profound, contradiction…)."""
    from perspicacite.models.rag import RAGRequest as RAGReq, RAGMode

    # Map string mode to RAGMode enum using the module-level constant
    rag_mode = RAG_MODE_MAP.get(request.mode, RAGMode.BASIC)

    logger.info(f"Using RAGEngine with mode: {rag_mode.value}")

    # Generate session_id if not provided
    session_id = request.session_id or str(uuid.uuid4())

    conv_hist = (
        [{"role": m.role, "content": m.content} for m in request.messages]
        if request.messages
        else None
    )

    # Create RAG request
    rag_request = RAGReq(
        query=request.query,
        kb_name=request.kb_name or "default",
        mode=rag_mode,
        stream=True,
        databases=request.databases,
        conversation_history=conv_hist,
        max_papers_retrieval=request.max_papers,
    )

    # Execute using RAGEngine streaming
    full_answer = ""
    sources = []

    try:
        async for event in app_state.rag_engine.query_stream(rag_request):
            if event.event == "status":
                # Forward status updates
                status_data = json.loads(event.data)
                # Include full status data (for literature survey session info, etc.)
                yield f"data: {json.dumps({'type': 'status', **status_data})}\n\n"

            elif event.event == "source":
                # Collect sources
                source_data = json.loads(event.data)
                sources.append(source_data)
                # Also forward to UI for display
                yield f"data: {json.dumps({'type': 'source', 'source': source_data})}\n\n"

            elif event.event == "content":
                # Accumulate answer content
                delta = json.loads(event.data)["delta"]
                full_answer += delta
                # Live token deltas for the UI (base64 avoids mid-chunk JSON breakage)
                token_payload = {
                    "type": "token",
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                    "delta_b64": base64.b64encode(delta.encode("utf-8")).decode("ascii"),
                }
                yield f"data: {json.dumps(token_payload, separators=(',', ':'))}\n\n"

            elif event.event == "done":
                # Send final answer as base64
                safe = {
                    "type": "answer",
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                    "content_b64": base64.b64encode(full_answer.encode("utf-8")).decode("ascii"),
                    "sources": sources,
                }
                yield f"data: {json.dumps(safe, separators=(',', ':'))}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            elif event.event == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': event.data})}\n\n"
                return

    except Exception as e:
        logger.error(f"RAG engine error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': f'Error in {rag_mode.value} mode: {str(e)}'})}\n\n"

    # End of stream (fallback if no done event)
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
