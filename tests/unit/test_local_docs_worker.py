"""local_docs worker dispatches per content type."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.integrations.local_docs import _ingest_files


class _Reg:
    def __init__(self):
        self.events = []
        self.finished = None

    async def publish(self, jid, ev):
        self.events.append(ev)

    async def finish(self, jid, res):
        self.finished = res

    async def fail(self, jid, err):
        self.failed = err


class _Emb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


class _VS:
    def __init__(self):
        self.added: list = []

    async def add_documents(self, collection, chunks):
        self.added.extend(chunks)


@pytest.mark.asyncio
async def test_worker_ingests_markdown_and_code(tmp_path, monkeypatch):
    md = tmp_path / "notes.md"
    md.write_text("# Top\n\nIntro\n\n## Sub\n\nDetail")
    py = tmp_path / "lib.py"
    py.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")

    fake_state = SimpleNamespace(
        config=SimpleNamespace(knowledge_base=SimpleNamespace(
            chunk_size=1000, chunk_overlap=200,
            markdown_heading_aware=True, code_language_aware=True,
        )),
        embedding_provider=_Emb(),
        vector_store=_VS(),
        pdf_parser=None,
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="perspicacite_local", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )
    reg = _Reg()
    await _ingest_files(
        kb_name="local",
        files=[md, py],
        app_state=fake_state,
        registry=reg,
        job_id="J1",
    )
    cts = {c.metadata.content_type for c in fake_state.vector_store.added}
    assert {"markdown", "code"} <= cts
    langs = {c.metadata.language for c in fake_state.vector_store.added if c.metadata.content_type == "code"}
    assert "python" in langs
    assert reg.finished is not None
