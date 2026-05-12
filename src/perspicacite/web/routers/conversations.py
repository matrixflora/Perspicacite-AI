"""Conversation CRUD routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from perspicacite.web.state import app_state


router = APIRouter()


@router.get("/api/conversations")
async def list_conversations(session_id: Optional[str] = None):
    """List all conversations (optionally filtered by session_id)."""
    if not app_state.session_store:
        return []

    # If no session_id provided, return all conversations
    if session_id:
        conversations = await app_state.session_store.list_conversations(session_id)
    else:
        # Get all conversations from all sessions
        import aiosqlite

        async with aiosqlite.connect(app_state.session_store.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM conversations ORDER BY updated_at DESC")
            conversations = [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "kb_name": r["kb_name"],
                    "session_id": r["session_id"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]

    return conversations


@router.get("/api/conversations/search")
async def search_conversations(q: str = ""):
    """Full-text search across saved conversations."""
    if not app_state.session_store:
        return {"results": []}
    if not q or not q.strip():
        return {"results": []}
    return {"results": await app_state.session_store.search_conversations(q.strip())}


@router.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get a specific conversation with all messages."""
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    conversation = await app_state.session_store.get_conversation(conv_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": conversation.id,
        "title": conversation.title,
        "kb_name": conversation.kb_name,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            }
            for m in conversation.messages
        ],
    }


@router.post("/api/conversations")
async def create_conversation(request: dict):
    """Create a new conversation."""
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    session_id = request.get("session_id", "default")
    kb_name = request.get("kb_name", "default")
    title = request.get("title", "New Conversation")

    conversation = await app_state.session_store.create_conversation(
        session_id=session_id,
        kb_name=kb_name,
        title=title,
    )

    return {
        "id": conversation.id,
        "title": conversation.title,
        "kb_name": conversation.kb_name,
        "session_id": session_id,
    }


@router.post("/api/conversations/{conv_id}/messages")
async def add_message(conv_id: str, request: dict):
    """Add a message to a conversation."""
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    from perspicacite.models.messages import Message

    message = Message(
        role=request.get("role", "user"),
        content=request.get("content", ""),
    )

    await app_state.session_store.add_message(conv_id, message)

    return {"status": "ok"}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and all its messages."""
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    success = await app_state.session_store.delete_conversation(conv_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conv_id}


@router.delete("/api/conversations")
async def delete_all_conversations():
    """Delete all conversations for the current user."""
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    count = await app_state.session_store.delete_all_conversations()
    return {"status": "deleted", "count": count}
