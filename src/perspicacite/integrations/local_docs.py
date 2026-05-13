"""Local-document ingestion: path validate + chunk dispatch + KB write."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import (
    _local_paper_id_for,
    chunk_document,
    infer_content_type,
)

logger = get_logger("perspicacite.local_docs")


class LocalDocsValidationError(ValueError):
    """Raised when a path fails validation."""


class LocalDocsDisabledError(RuntimeError):
    """Raised when local_docs.allowed_roots is empty (server-side path entry disabled)."""


def validate_local_path(raw_path: str, *, allowed_roots: list[Path]) -> Path:
    """Reject relative paths / '..' / outside allowed_roots. Return resolved Path."""
    if not allowed_roots:
        raise LocalDocsDisabledError(
            "local_docs.allowed_roots is empty — server-side path ingest is disabled"
        )
    if not os.path.isabs(raw_path):
        raise LocalDocsValidationError(f"path must be absolute: {raw_path}")
    if ".." in Path(raw_path).parts:
        raise LocalDocsValidationError(f"path must not contain '..': {raw_path}")
    p = Path(raw_path).resolve()
    if not p.exists():
        raise LocalDocsValidationError(f"path does not exist: {raw_path}")
    for root in allowed_roots:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise LocalDocsValidationError(
        f"path {raw_path} is not under any local_docs.allowed_roots"
    )


def expand_paths(paths: list[Path], *, recursive: bool) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            if recursive:
                out.extend(f for f in p.rglob("*") if f.is_file())
        else:
            out.append(p)
    return out


def _paper_for_file(path: Path) -> Paper:
    return Paper(
        id=_local_paper_id_for(path),
        title=path.name,
        source=PaperSource.LOCAL,
    )


async def _read_text(path: Path, content_type: str, pdf_parser) -> str | None:
    if content_type == "pdf":
        if pdf_parser is None:
            return None
        parsed = await pdf_parser.parse(path)
        return parsed.text or None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("local_docs_read_failed", path=str(path), error=str(exc))
        return None


async def _ingest_files(
    *,
    kb_name: str,
    files: list[Path],
    app_state,
    registry,
    job_id: str,
) -> dict[str, Any]:
    try:
        kb = await app_state.session_store.get_kb_metadata(kb_name)
        if kb is None:
            await registry.fail(job_id, f"KB '{kb_name}' not found")
            return {}
        kb_cfg = app_state.config.knowledge_base
        total_chunks = 0
        for idx, fp in enumerate(files):
            content_type, language = infer_content_type(fp)
            paper = _paper_for_file(fp)
            text = await _read_text(fp, content_type, app_state.pdf_parser)
            if not text:
                await registry.publish(job_id, {
                    "type": "progress", "done": idx + 1, "file": str(fp), "status": "empty",
                })
                continue
            chunks = await chunk_document(
                text, paper,
                content_type=content_type, language=language, config=kb_cfg,
            )
            # ChunkMetadata is frozen — recreate with source_file_path set
            for c in chunks:
                c.metadata = ChunkMetadata(
                    **{**c.metadata.model_dump(), "source_file_path": str(fp.resolve())}
                )
            if chunks:
                texts = [c.text for c in chunks]
                embeds = await app_state.embedding_provider.embed(texts)
                for c, e in zip(chunks, embeds, strict=True):
                    c.embedding = e
                await app_state.vector_store.add_chunks(kb.collection_name, chunks)
                total_chunks += len(chunks)
            await registry.publish(job_id, {
                "type": "progress", "done": idx + 1, "file": str(fp),
                "status": "embedded", "chunks": len(chunks),
            })
        kb.chunk_count += total_chunks
        await app_state.session_store.save_kb_metadata(kb)
        result = {"added_chunks": total_chunks, "files": len(files)}
        await registry.finish(job_id, result)
        return result
    except Exception as exc:
        logger.error("local_docs_ingest_failed", error=str(exc))
        await registry.fail(job_id, str(exc))
        raise


async def ingest_local_documents(
    *,
    kb_name: str,
    paths: list[Path],
    app_state,
    registry,
    job_id: str,
    recursive: bool = True,
) -> dict[str, Any]:
    """Top-level entry used by routers, CLI, and MCP."""
    expanded = expand_paths(paths, recursive=recursive)
    return await _ingest_files(
        kb_name=kb_name, files=expanded, app_state=app_state,
        registry=registry, job_id=job_id,
    )
