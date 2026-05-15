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
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from perspicacite.models.rag import RAGMode
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting
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
    kb_names: Optional[List[str]] = Field(
        default=None,
        description="Multiple knowledge bases to query together (embedding-model-compatible KBs only)",
    )
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
                logger.warning("non_streaming_pipeline_error: %s", error_message)

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
    assistant_message_id_outer: Optional[str] = None

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
                # Collect assistant content and message_id for saving
                if event.startswith("data:"):
                    try:
                        data = json.loads(event[5:].strip())
                        if data.get("type") == "answer":
                            content_b64 = data.get("content_b64")
                            if content_b64:
                                assistant_content = base64.b64decode(content_b64).decode("utf-8")
                            new_msg_id = data.get("message_id")
                            if new_msg_id:
                                assistant_message_id_outer = new_msg_id
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
            msg_id = assistant_message_id_outer or str(uuid.uuid4())
            await app_state.session_store.add_message(
                conversation_id,
                Message(id=msg_id, role="assistant", content=assistant_content),
            )
            logger.info(f"Saved conversation messages to {conversation_id}")
        except Exception as e:
            logger.warning(f"Failed to save assistant message: {e}")


async def _copyright_filter_for_router(
    *, full_answer: str, sources: list[dict[str, Any]],
) -> str:
    """Router-level copyright filter applied to the assembled answer.

    The sources list emitted to the client contains paper metadata
    (title, doi, year, relevance_score) but typically not full_text.
    Without the original chunk text we can only run the detector on
    whatever is in ``sources[i].get("text")`` / ``chunk_text`` (which
    some modes do populate for citation snippets).

    For action=log, this is a final observability hook. For
    quote/strip/rewrite, per-mode synthesis is the better place to run
    the filter (it has the actual chunk full_text) — see
    rag/modes/basic.py::_apply_copyright_filter.
    """
    if not full_answer or not sources:
        return full_answer
    try:
        from perspicacite.rag.copyright_filter import CopyrightFilter
        cf_cfg = getattr(getattr(app_state, "config", None), "copyright_filter", None)
        if cf_cfg is None or not getattr(cf_cfg, "enabled", True):
            return full_answer
        sources_for_check = [
            {
                "text": s.get("chunk_text") or s.get("text") or "",
                "title": s.get("title"),
            }
            for s in sources
        ]
        cf = CopyrightFilter(
            enabled=cf_cfg.enabled,
            action=getattr(cf_cfg, "action", "log"),
            min_ngram=getattr(cf_cfg, "min_ngram", 8),
            llm_client=getattr(app_state, "llm_client", None),
            rewrite_model=getattr(cf_cfg, "rewrite_model", "claude-haiku-4-5"),
            rewrite_provider=getattr(cf_cfg, "rewrite_provider", "anthropic"),
        )
        return await cf.apply(full_answer, sources_for_check)
    except Exception as exc:
        logger.warning(f"router_copyright_filter_inner_failed: {exc}")
        return full_answer


async def _stream_agentic(request: ChatRequest, conversation_id: Optional[str] = None):
    """Stream using AgenticOrchestrator."""
    assistant_message_id = str(uuid.uuid4())

    collector = ProvenanceCollector(
        conversation_id=conversation_id,
        message_id=assistant_message_id,
        rag_mode="agentic",
        request_params={
            "kb_name": request.kb_name,
            "kb_names": getattr(request, "kb_names", None),
        },
    )

    # See engine.py:query_stream — `with collecting()` inside an async
    # generator consumed by StreamingResponse can raise
    # "Token was created in a different Context" when the contextvar token
    # is reset across asyncio Context boundaries. Set without resetting.
    from perspicacite.provenance.context import set_collector
    set_collector(collector)

    # Accumulate `papers_found` events to populate `answer.sources`.
    # The orchestrator emits `papers_found` AFTER `answer` (so the
    # browser UI can append citations without overwriting the answer
    # innerHTML), but the chat-route contract puts sources on the
    # answer event. We buffer the answer event until papers_found
    # arrives or the stream ends, then merge.
    accumulated_papers: list[dict[str, Any]] = []
    pending_answer_event: dict[str, Any] | None = None

    def _emit_answer(content: str, sess_id: str | None) -> str:
        sources = [
            {
                "title": p.get("title"),
                "authors": (
                    ", ".join(p["authors"]) if isinstance(p.get("authors"), list)
                    else p.get("authors")
                ),
                "year": p.get("year"),
                "doi": (
                    p["doi"].replace("https://doi.org/", "")
                    if isinstance(p.get("doi"), str)
                    else p.get("doi")
                ),
                "url": p.get("url"),
                "relevance_score": p.get("relevance_score"),
                "kb_name": p.get("kb_name"),
            }
            for p in accumulated_papers
        ]
        safe = {
            "type": "answer",
            "session_id": sess_id,
            "conversation_id": conversation_id,
            "message_id": assistant_message_id,
            "content_b64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sources": sources,
        }
        return f"data: {json.dumps(safe, separators=(',', ':'))}\n\n"

    async for event in app_state.orchestrator.chat(
        query=request.query,
        session_id=request.session_id,
        kb_name=request.kb_name,
        stream=True,
        max_papers_to_download=request.max_papers_to_download,
    ):
        if event.get("type") == "papers_found":
            for p in event.get("papers") or []:
                key = (p.get("doi") or p.get("id") or p.get("title") or "").strip()
                if not key:
                    continue
                if any(
                    (q.get("doi") or q.get("id") or q.get("title") or "").strip() == key
                    for q in accumulated_papers
                ):
                    continue
                accumulated_papers.append(p)
            # Flush any pending answer event now that we have papers.
            if pending_answer_event is not None:
                pending = pending_answer_event
                pending_answer_event = None
                yield _emit_answer(pending["content"], pending["session_id"])
            # Forward the papers_found event for UI rendering.
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
            continue

        if event.get("type") == "answer":
            # Buffer until papers_found arrives (or stream ends).
            pending_answer_event = {
                "content": event.get("content") or "",
                "session_id": event.get("session_id"),
            }
            continue

        yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

    # If no papers_found came after the answer, flush the buffered answer
    # event so the client still gets a response (with empty sources).
    if pending_answer_event is not None:
        yield _emit_answer(
            pending_answer_event["content"], pending_answer_event["session_id"]
        )

    # End of stream
    yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_message_id})}\n\n"

    # Persist provenance record — best-effort, never raise
    if app_state.provenance_store is not None:
        try:
            await app_state.provenance_store.save(collector.finalize())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"provenance_save_failed (agentic): {exc}")


async def _stream_rag_mode(request: ChatRequest, conversation_id: Optional[str] = None):
    """Stream using RAGEngine with selected mode (basic, advanced, profound, contradiction…)."""
    from perspicacite.models.rag import RAGRequest as RAGReq, RAGMode

    # Map string mode to RAGMode enum using the module-level constant
    rag_mode = RAG_MODE_MAP.get(request.mode, RAGMode.BASIC)

    logger.info(f"Using RAGEngine with mode: {rag_mode.value}")

    # Pre-generate assistant message id so it can be threaded into the engine
    # and included in SSE frames so the UI and provenance rows share the same id.
    assistant_message_id = str(uuid.uuid4())

    # Generate session_id if not provided
    session_id = request.session_id or str(uuid.uuid4())

    conv_hist = (
        [{"role": m.role, "content": m.content} for m in request.messages]
        if request.messages
        else None
    )

    # Determine effective kb_name / kb_names for the RAG request
    effective_kb_name = request.kb_name or "default"
    effective_kb_names: Optional[List[str]] = None

    # `kb_name="auto"` (or `kb_names=["auto"]`) routes the query: pick the
    # top-N most-relevant KBs by description + sampled paper titles, then
    # query them in parallel via the multi-KB path. BM25 by default
    # (no LLM cost); set rag_modes.route_method='llm' to use the LLM
    # router instead (one cheap call).
    is_auto = (
        (request.kb_name or "").strip().lower() == "auto"
        or (request.kb_names and len(request.kb_names) == 1
            and (request.kb_names[0] or "").strip().lower() == "auto")
    )
    if is_auto and app_state.session_store:
        from perspicacite.rag.kb_router import auto_route_kbs
        all_kbs = await app_state.session_store.list_kbs()
        cfg_rag = getattr(getattr(app_state, "config", None), "rag_modes", None)
        method = getattr(cfg_rag, "route_method", "bm25") if cfg_rag else "bm25"
        top_k = getattr(cfg_rag, "route_top_k", 3) if cfg_rag else 3
        threshold = getattr(cfg_rag, "route_threshold", 0.1) if cfg_rag else 0.1
        from perspicacite.llm.client import resolve_stage_model
        rp, rm = resolve_stage_model(getattr(app_state, "config", None), "routing")
        hits = await auto_route_kbs(
            query=request.query, kb_metas=all_kbs,
            vector_store=app_state.vector_store, method=method,
            top_k=top_k, score_threshold=threshold,
            llm_client=getattr(app_state, "llm_client", None),
            llm_model=rm, llm_provider=rp,
        )
        if not hits:
            yield (
                "data: " + json.dumps({
                    "type": "error",
                    "message": (
                        "auto-routing found no relevant KBs. Try a more "
                        "specific query, or specify kb_name / kb_names "
                        "directly."
                    ),
                }) + "\n\n"
            )
            return
        # Surface the routing decision to the client so the user sees
        # which KBs were picked + why (BM25 has no reason field).
        yield (
            "data: " + json.dumps({
                "type": "kb_route",
                "method": method,
                "hits": [h.to_dict() for h in hits],
            }) + "\n\n"
        )
        # If only one KB matched, route as single-KB; else multi-KB
        picked = [h.kb_name for h in hits]
        if len(picked) == 1:
            effective_kb_name = picked[0]
        else:
            # Run the same embedding-compat guard the manual multi-KB
            # path uses.
            from perspicacite.retrieval.multi_kb import check_embedding_compat
            metas = [await app_state.session_store.get_kb_metadata(n) for n in picked]
            compat_msg = check_embedding_compat(metas)
            if compat_msg:
                yield (
                    "data: " + json.dumps({
                        "type": "error", "message": compat_msg,
                    }) + "\n\n"
                )
                return
            effective_kb_names = picked

    elif request.kb_names and len(request.kb_names) > 1 and app_state.session_store:
        from perspicacite.retrieval.multi_kb import check_embedding_compat

        metas = [await app_state.session_store.get_kb_metadata(n) for n in request.kb_names]
        missing = next((request.kb_names[i] for i, m in enumerate(metas) if m is None), None)
        if missing is not None:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Knowledge base not found: {missing}'})}\n\n"
            return
        compat_msg = check_embedding_compat(metas)
        if compat_msg:
            yield f"data: {json.dumps({'type': 'error', 'message': compat_msg})}\n\n"
            return
        effective_kb_names = request.kb_names
    elif request.kb_names and len(request.kb_names) == 1:
        effective_kb_name = request.kb_names[0]

    # Resolve provider/model from server-side config so the chat router
    # respects llm.default_provider / llm.default_model from config.yml
    # rather than the hard-coded RAGRequest defaults (deepseek).
    cfg_llm = getattr(app_state, "config", None)
    default_provider = "deepseek"
    default_model = "deepseek-chat"
    if cfg_llm is not None and getattr(cfg_llm, "llm", None) is not None:
        default_provider = cfg_llm.llm.default_provider or default_provider
        default_model = cfg_llm.llm.default_model or default_model

    # Create RAG request
    rag_request = RAGReq(
        query=request.query,
        kb_name=effective_kb_name,
        kb_names=effective_kb_names,
        mode=rag_mode,
        stream=True,
        databases=request.databases,
        conversation_history=conv_hist,
        max_papers_retrieval=request.max_papers,
        provider=default_provider,
        model=default_model,
    )
    # Execute using RAGEngine streaming
    full_answer = ""
    sources = []

    try:
        async for event in app_state.rag_engine.query_stream(
            rag_request,
            message_id=assistant_message_id,
            conversation_id=conversation_id,
        ):
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
                # Defense-in-depth copyright filter: run BEFORE we emit
                # the final answer event so any rewritten content goes
                # into the same answer envelope (rather than as a
                # follow-up revision event the client might miss).
                # Sources here carry titles + relevance_score but not
                # full_text; the per-mode synthesis path already ran the
                # filter against full chunk text when configured for
                # quote/strip/rewrite actions. This emits a log-only
                # warning event when verbatim copies still escape.
                try:
                    full_answer = await _copyright_filter_for_router(
                        full_answer=full_answer, sources=sources,
                    )
                except Exception as _exc:  # noqa: BLE001
                    logger.warning(f"router_copyright_filter_failed: {_exc}")
                # Send final answer as base64
                safe = {
                    "type": "answer",
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                    "message_id": assistant_message_id,
                    "content_b64": base64.b64encode(full_answer.encode("utf-8")).decode("ascii"),
                    "sources": sources,
                }
                yield f"data: {json.dumps(safe, separators=(',', ':'))}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_message_id})}\n\n"
                return

            elif event.event == "code_excerpt":
                # Sub-project C: forward code-excerpt attachment to UI.
                try:
                    payload = json.loads(event.data)
                    yield f"data: {json.dumps({'type': 'code_excerpt', **payload})}\n\n"
                except Exception as _exc:
                    logger.warning(f"code_excerpt_forward_failed: {_exc}")

            elif event.event == "figure_ref":
                # Sub-project C: forward figure-ref attachment to UI.
                try:
                    payload = json.loads(event.data)
                    yield f"data: {json.dumps({'type': 'figure_ref', **payload})}\n\n"
                except Exception as _exc:
                    logger.warning(f"figure_ref_forward_failed: {_exc}")

            elif event.event == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': event.data})}\n\n"
                return

    except Exception as e:
        logger.error(f"RAG engine error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': f'Error in {rag_mode.value} mode: {str(e)}'})}\n\n"

    # End of stream (fallback if no done event)
    yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_message_id})}\n\n"
