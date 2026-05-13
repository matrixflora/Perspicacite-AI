"""ProvenanceStore — writes to SQLite + optional JSONL sidecar.

P1 (Task 1.2) only writes the SQLite row (llm_calls_index empty). Task 2.1
will add JSONL sidecar writes for full prompt/response payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.provenance.store")


class ProvenanceStore:
    def __init__(self, db_path: str | Path, sidecar_dir: str | Path):
        self.db_path = Path(db_path)
        self.sidecar_dir = Path(sidecar_dir)
        self.sidecar_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, record: dict[str, Any]) -> None:
        message_id = record.get("message_id")
        if not message_id:
            logger.warning("provenance_save_no_message_id")
            return
        conv_id = record.get("conversation_id")
        llm_calls: list[dict[str, Any]] = list(record.get("llm_calls") or [])
        llm_calls_index: list[dict[str, Any]] = []
        sidecar_path: str | None = None
        try:
            if conv_id and llm_calls:
                sidecar_path = f"{conv_id}.jsonl"
                target = self.sidecar_dir / sidecar_path
                self.sidecar_dir.mkdir(parents=True, exist_ok=True)
                with target.open("ab") as f:
                    for call in llm_calls:
                        offset = f.tell()
                        line = (json.dumps(call) + "\n").encode("utf-8")
                        f.write(line)
                        llm_calls_index.append({
                            "stage_label": call.get("stage_label"),
                            "provider": call.get("provider"),
                            "model": call.get("model"),
                            "prompt_tokens": call.get("prompt_tokens", 0),
                            "completion_tokens": call.get("completion_tokens", 0),
                            "latency_ms": call.get("latency_ms", 0.0),
                            "ts": call.get("ts"),
                            "offset": offset,
                        })
            else:
                # No conversation id → keep payload inline in the index entries
                for call in llm_calls:
                    entry = dict(call)
                    entry["offset"] = None
                    llm_calls_index.append(entry)
        except Exception as exc:
            logger.warning("provenance_sidecar_write_failed", error=str(exc), message_id=message_id)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO provenance
                        (message_id, conversation_id, rag_mode, request_params,
                         retrieval_events, mode_trace, llm_calls_index, sidecar_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id, conv_id,
                        record.get("rag_mode", "unknown"),
                        json.dumps(record.get("request_params") or {}),
                        json.dumps(record.get("retrieval_events") or []),
                        json.dumps(record.get("mode_trace") or []),
                        json.dumps(llm_calls_index),
                        sidecar_path,
                    ),
                )
                await db.commit()
        except Exception as exc:  # best-effort
            logger.warning("provenance_save_failed", error=str(exc), message_id=message_id)

    async def get_for_message(self, message_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM provenance WHERE message_id = ?", (message_id,)
            )
            row = await cur.fetchone()
        if not row:
            return None
        return _row_to_record(row, sidecar_dir=self.sidecar_dir)

    async def get_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM provenance WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            )
            rows = await cur.fetchall()
        return [_row_to_record(r, sidecar_dir=self.sidecar_dir) for r in rows]


def _row_to_record(row: Any, *, sidecar_dir: Path) -> dict[str, Any]:
    index = json.loads(row["llm_calls_index"] or "[]")
    sidecar_path = row["sidecar_path"]
    llm_calls = _resolve_llm_calls(index, sidecar_path, sidecar_dir)
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "rag_mode": row["rag_mode"],
        "request_params": json.loads(row["request_params"] or "{}"),
        "retrieval_events": json.loads(row["retrieval_events"] or "[]"),
        "mode_trace": json.loads(row["mode_trace"] or "[]"),
        "llm_calls_index": index,
        "llm_calls": llm_calls,
        "created_at": row["created_at"],
    }


def _resolve_llm_calls(
    index: list[dict[str, Any]], sidecar_path: str | None, sidecar_dir: Path
) -> list[dict[str, Any]]:
    if not index:
        return []
    if not sidecar_path:
        # Inline payloads (no conversation id): strip the 'offset' marker
        return [{k: v for k, v in entry.items() if k != "offset"} for entry in index]
    p = sidecar_dir / sidecar_path
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open("rb") as f:
        for entry in index:
            offset = entry.get("offset")
            if offset is None:
                continue
            f.seek(offset)
            line = f.readline()
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
