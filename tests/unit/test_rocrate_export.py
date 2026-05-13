from __future__ import annotations

import io
import json
import zipfile

from perspicacite.provenance.rocrate import build_rocrate_bundle


def test_rocrate_bundle_structure() -> None:
    conversation = {
        "id": "conv-1",
        "title": "Q&A on microbiome",
        "kb_name": "default",
        "created_at": "2026-05-13T10:00:00",
    }
    messages = [
        {"id": "u1", "role": "user", "content": "What is X?", "timestamp": "..."},
        {
            "id": "a1", "role": "assistant", "content": "X is …",
            "timestamp": "...",
            "sources": [{"doi": "10.1/a", "title": "Paper A", "year": 2024, "journal": "J",
                         "kb_name": "default", "content_type": "full_text"}],
        },
    ]
    provenance_records = [
        {
            "message_id": "a1",
            "conversation_id": "conv-1",
            "rag_mode": "basic",
            "retrieval_events": [{"doi": "10.1/a", "title": "Paper A", "score": 0.9}],
            "mode_trace": [{"step": "retrieve", "detail": {"count": 1}}],
            "llm_calls_index": [{"stage_label": "basic.answer", "model": "deepseek-chat"}],
            "request_params": {"kb_name": "default", "top_k": 5},
        }
    ]
    llm_calls_jsonl = b'{"stage_label":"basic.answer","model":"deepseek-chat"}\n'

    blob = build_rocrate_bundle(
        conversation=conversation,
        messages=messages,
        conversation_markdown="# Conv\n\nuser: hi\n",
        provenance_records=provenance_records,
        llm_calls_jsonl=llm_calls_jsonl,
    )
    assert isinstance(blob, bytes) and len(blob) > 0
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    assert "ro-crate-metadata.json" in names
    assert "conversation.md" in names
    assert "sources.json" in names
    assert "provenance/answer-a1.json" in names
    assert "provenance/llm-calls.jsonl" in names

    meta = json.loads(z.read("ro-crate-metadata.json"))
    assert meta["@context"]
    assert isinstance(meta["@graph"], list)
    types = {e.get("@type") for e in meta["@graph"]}
    assert "Dataset" in types
    assert "ScholarlyArticle" in types
    assert "CreateAction" in types

    sources = json.loads(z.read("sources.json"))
    assert sources[0]["doi"] == "10.1/a"


def test_rocrate_bundle_handles_empty_provenance() -> None:
    blob = build_rocrate_bundle(
        conversation={"id": "c0", "title": "T", "created_at": "now"},
        messages=[],
        conversation_markdown="empty\n",
        provenance_records=[],
        llm_calls_jsonl=b"",
    )
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert "ro-crate-metadata.json" in z.namelist()
    assert "provenance/llm-calls.jsonl" in z.namelist()  # zero-byte file is fine
    sources = json.loads(z.read("sources.json"))
    assert sources == []


def test_rocrate_bundle_dedupes_sources_by_doi() -> None:
    messages = [
        {"id": "a1", "role": "assistant", "content": "X",
         "sources": [{"doi": "10.1/a", "title": "A"}, {"doi": "10.1/a", "title": "A"}]},
        {"id": "a2", "role": "assistant", "content": "Y",
         "sources": [{"doi": "10.1/a", "title": "A"}, {"doi": "10.1/b", "title": "B"}]},
    ]
    blob = build_rocrate_bundle(
        conversation={"id": "c", "title": "T", "created_at": "now"},
        messages=messages,
        conversation_markdown="",
        provenance_records=[],
        llm_calls_jsonl=b"",
    )
    z = zipfile.ZipFile(io.BytesIO(blob))
    sources = json.loads(z.read("sources.json"))
    dois = [s.get("doi") for s in sources]
    assert dois.count("10.1/a") == 1
    assert "10.1/b" in dois
