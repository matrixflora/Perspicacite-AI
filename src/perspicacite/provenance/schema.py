"""Schema for the ``provenance`` table.

Single source of truth shared by :mod:`perspicacite.provenance.store`
and :mod:`perspicacite.memory.session_store` so a ``ProvenanceStore``
can be used standalone (without a ``SessionStore`` having booted first).
"""

PROVENANCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provenance (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    rag_mode TEXT NOT NULL,
    request_params TEXT DEFAULT '{}',
    retrieval_events TEXT DEFAULT '[]',
    mode_trace TEXT DEFAULT '[]',
    llm_calls_index TEXT DEFAULT '[]',
    sidecar_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_provenance_conversation
    ON provenance(conversation_id);
"""
