"""Read an ASB-shaped capsule directory into a Perspicacité KB."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import Author, Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document

logger = get_logger("perspicacite.capsule_reader")


def is_capsule_dir(path) -> bool:
    """Return True iff ``path`` is a directory containing ``metadata.json``
    with a non-empty ``capsule_version`` field.
    """
    p = Path(path)
    if not p.is_dir():
        return False
    meta = p / "metadata.json"
    if not meta.is_file():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("capsule_version"))


def _author_from_dict(a: dict[str, Any]) -> Author:
    """Build an Author from a capsule metadata author dict.

    Capsule metadata uses ``{"family": "...", "given": "..."}`` (CSL-JSON
    style); ``Author`` requires a ``name`` field, so synthesise one when
    absent.
    """
    given = a.get("given")
    family = a.get("family")
    name = a.get("name")
    if not name:
        if given and family:
            name = f"{given} {family}"
        elif family:
            name = family
        elif given:
            name = given
        else:
            name = "Unknown"
    return Author(name=name, given=given, family=family)


async def ingest_capsule(
    *,
    capsule_dir,
    kb_name: str,
    app_state,
    registry,
    job_id: str,
    finalize: bool = True,
) -> dict[str, Any]:
    """Ingest an ASB-shaped capsule directory into a Perspicacité KB.

    The capsule's ``metadata.json`` provides paper identity. Text is taken
    from ``text/blocks.jsonl`` when present (Perspicacité-native — one JSON
    object per line, fields: ``section``, ``page``, ``content``,
    ``figure_refs``, ``table_refs``); otherwise from
    ``evidence/source_snippets.md`` (ASB-style); otherwise the function
    logs a warning and finishes with zero chunks. Resource IDs from
    ``resources.json`` are propagated onto every chunk.

    Args:
        capsule_dir: Filesystem path to the capsule directory.
        kb_name: Name of the destination KB (must already exist).
        app_state: Provides session_store, embedding_provider, vector_store,
            and config.knowledge_base.
        registry: Job registry exposing ``publish/finish/fail``.
        job_id: Job identifier for progress events.
        finalize: When ``True`` (default) call ``registry.finish`` at the end.
            Set to ``False`` for the multi-capsule caller in Task 16 so it can
            finalize once after all capsules are ingested.

    Returns:
        ``{"added_chunks": int, "files": int}`` (or ``{}`` when the function
        fails through ``registry.fail``).
    """
    capsule_dir = Path(capsule_dir)
    meta_path = capsule_dir / "metadata.json"
    if not meta_path.is_file():
        await registry.fail(job_id, f"not a capsule directory: {capsule_dir}")
        return {}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        await registry.fail(job_id, f"corrupt capsule metadata: {exc}")
        return {}

    paper_id = meta.get("paper_id") or f"local:{capsule_dir.name}"
    authors = [_author_from_dict(a) for a in (meta.get("authors") or [])]
    paper = Paper(
        id=paper_id,
        title=meta.get("title") or capsule_dir.name,
        authors=authors,
        year=meta.get("year"),
        doi=meta.get("doi"),
        source=PaperSource.LOCAL,
    )

    blocks_path = capsule_dir / "text" / "blocks.jsonl"
    snippets_path = capsule_dir / "evidence" / "source_snippets.md"

    # Group block text by section; remember figure_refs per section.
    section_to_text: dict[str, str] = {}
    section_figure_refs: dict[str, list[str]] = {}

    if blocks_path.is_file():
        for line in blocks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                b = json.loads(line)
            except json.JSONDecodeError:
                continue
            sec = b.get("section") or "other"
            content = b.get("content") or ""
            prior = section_to_text.get(sec, "")
            section_to_text[sec] = (
                (prior + "\n\n" + content) if prior else content
            ).strip()
            for f in (b.get("figure_refs") or []):
                refs = section_figure_refs.setdefault(sec, [])
                if f not in refs:
                    refs.append(f)
    elif snippets_path.is_file():
        section_to_text["other"] = snippets_path.read_text(encoding="utf-8")
    else:
        logger.warning("capsule_no_text_source", capsule=str(capsule_dir))
        result = {"added_chunks": 0, "files": 1}
        if finalize:
            await registry.finish(job_id, result)
        return result

    # Load resources.json (optional) and collect all resource_ids.
    resources: list[dict[str, Any]] = []
    res_path = capsule_dir / "resources.json"
    if res_path.is_file():
        try:
            loaded = json.loads(res_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                resources = loaded
        except json.JSONDecodeError:
            resources = []
    all_resource_ids = [
        r.get("resource_id") for r in resources if r.get("resource_id")
    ]

    kb = await app_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        await registry.fail(job_id, f"KB '{kb_name}' not found")
        return {}

    kb_cfg = app_state.config.knowledge_base
    all_chunks = []
    for section, text in section_to_text.items():
        if not text.strip():
            continue
        chunks = await chunk_document(
            text,
            paper,
            content_type="text",
            language=None,
            config=kb_cfg,
        )
        fig_refs = list(section_figure_refs.get(section, []))
        for c in chunks:
            base = c.metadata.model_dump()
            base["source_section"] = section
            base["figure_refs"] = list(fig_refs)
            base["resource_refs"] = list(all_resource_ids)
            c.metadata = ChunkMetadata(**base)
        all_chunks.extend(chunks)

    # Sub-project A: best-effort symbol-index sidecar write for code chunks.
    try:
        _kb_dir = Path(app_state.config.capsule.root) / kb_name
        from perspicacite.pipeline.symbol_index import write_chunks_symbols
        write_chunks_symbols(kb_dir=_kb_dir, chunks=all_chunks)
    except Exception as _sym_exc:  # never break ingest on sidecar failure
        logger.warning(
            "symbol_index_write_failed",
            capsule=str(capsule_dir), error=str(_sym_exc)[:200],
        )

    total = 0
    if all_chunks:
        texts = [c.text for c in all_chunks]
        embeds = await app_state.embedding_provider.embed(texts)
        for c, e in zip(all_chunks, embeds, strict=True):
            c.embedding = e
        await app_state.vector_store.add_documents(kb.collection_name, all_chunks)
        total = len(all_chunks)

    kb.chunk_count += total
    await app_state.session_store.save_kb_metadata(kb)

    await registry.publish(
        job_id,
        {
            "type": "progress",
            "done": 1,
            "file": str(capsule_dir),
            "status": "ingested",
            "chunks": total,
        },
    )
    result = {"added_chunks": total, "files": 1}
    if finalize:
        await registry.finish(job_id, result)
    return result
