"""Top-level orchestrator for GitHub repo + skill-bundle ingestion.

Three public entry points:

- :func:`ingest_github_repo` — ingest any repo URL or local path
- :func:`ingest_skill_bundle` — ingest a single skill bundle (with optional
  linked-paper ingest via DOI)
- :func:`ingest_skill_bundles_batch` — ingest every bundle in a manifest list
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.github.bundle import (
    BundleManifest,
    extract_links_from_text,
)
from perspicacite.pipeline.github.chunk_producer import papers_from_directory

logger = get_logger("perspicacite.pipeline.github_kb")


@dataclass
class IngestSummary:
    bundle_name: str | None = None
    files_added: int = 0
    chunks_added: int = 0
    linked_papers_added: int = 0
    linked_papers_failed: int = 0
    errors: list[str] = field(default_factory=list)


async def ingest_github_repo(
    *,
    source: str | Path,
    kb_name: str,
    config: Any,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    ingest_linked_papers: bool = True,
    commit_sha: str = "unknown",
) -> IngestSummary:
    """Ingest a GitHub repository (URL or local path) into a KB.

    When ``source`` is a URL, fetches via :class:`GitHubFetcher`.
    When it's a Path, uses the directory directly.
    """
    source_str = str(source)
    source_path: Path | None = None

    if not source_str.startswith("https://"):
        source_path = Path(source)
    sha = commit_sha

    if source_path is None:
        from perspicacite.pipeline.github.fetcher import GitHubFetcher, parse_repo_url
        ref = parse_repo_url(source_str)
        token = None
        if hasattr(config, "github") and config.github.token_env_var:
            import os
            token = os.environ.get(config.github.token_env_var)
        if hasattr(config, "github"):
            cache_dir = Path(config.github.cache_dir)
        else:
            cache_dir = Path("data/github_cache")
        fetcher = GitHubFetcher(token=token, cache_dir=cache_dir)
        source_path, sha = await fetcher.fetch(ref)

    manifest = BundleManifest.from_directory(source_path)
    return await ingest_skill_bundle(
        source=source_path,
        kb_name=kb_name,
        config=config,
        vector_store=vector_store,
        embedding_service=embedding_service,
        session_store=session_store,
        ingest_linked_papers=ingest_linked_papers,
        commit_sha=sha,
        _manifest=manifest,
    )


async def ingest_skill_bundle(
    *,
    source: Path,
    kb_name: str | None,
    config: Any,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    ingest_linked_papers: bool = True,
    commit_sha: str = "unknown",
    _manifest: BundleManifest | None = None,
) -> IngestSummary:
    """Ingest a single skill bundle directory.

    If ``kb_name`` is None, uses ``bundle.name`` from the manifest.
    """
    manifest = _manifest or BundleManifest.from_directory(source)
    effective_kb = kb_name or manifest.name
    summary = IngestSummary(bundle_name=manifest.name)

    # 1. Produce Paper fixtures from directory files
    papers = papers_from_directory(source, manifest, commit_sha)
    summary.files_added = len(papers)

    # 2. Store via DynamicKnowledgeBase
    if papers:
        try:
            from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
            dkb = DynamicKnowledgeBase(
                vector_store,
                embedding_service,
            )
            chunks_added = await dkb.add_papers(papers)
            summary.chunks_added = chunks_added or 0
        except Exception as exc:
            logger.error("github_kb_add_papers_failed", error=str(exc))
            summary.errors.append(f"add_papers: {exc}")

    # 3. Collect linked paper DOIs
    if ingest_linked_papers:
        # From manifest papers section
        paper_refs = manifest.collect_paper_refs()
        # From README text
        if manifest.readme_text:
            bag = extract_links_from_text(manifest.readme_text)
            paper_refs += [("doi", d) for d in bag.dois]
            paper_refs += [("arxiv", a) for a in bag.arxiv_ids]
            paper_refs += [("pmc", p) for p in bag.pmc_ids]

        if paper_refs:
            dois = [ref_id for _, ref_id in paper_refs]
            try:
                import types

                from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb

                # Build a minimal app_state-compatible namespace for ingest_dois_into_kb
                # which expects an app_state with vector_store, embedding_provider,
                # session_store, config, and pdf_parser attributes.
                app_state = types.SimpleNamespace(
                    vector_store=vector_store,
                    embedding_provider=embedding_service,
                    session_store=session_store,
                    config=config,
                    pdf_parser=None,
                )
                result = await ingest_dois_into_kb(
                    app_state,
                    effective_kb,
                    dois,
                )
                added = result.get("added_papers", 0) if isinstance(result, dict) else 0
                summary.linked_papers_added = added
            except Exception as exc:
                logger.error("github_kb_linked_papers_failed", error=str(exc))
                summary.errors.append(f"ingest_dois: {exc}")
                summary.linked_papers_failed = len(paper_refs)

    # 4. Emit KB log events for external (non-paper) URLs
    if manifest.readme_text:
        bag = extract_links_from_text(manifest.readme_text)
        if bag.urls:
            try:
                from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter
                log_dir = None
                if hasattr(config, "knowledge_base") and hasattr(config.knowledge_base, "log_dir"):
                    log_dir = config.knowledge_base.log_dir
                if log_dir:
                    kb_log = KBLogWriter(path=Path(log_dir) / f"{effective_kb}.jsonl")
                    for url in bag.urls[:20]:  # cap at 20
                        kb_log.append(KBEvent(
                            event="paper_added",
                            kb_name=effective_kb,
                            paper_id=url,
                            source_command="ingest_skill_bundle",
                            extra={"url": url},
                        ))
            except Exception:
                pass  # KB log is best-effort

    return summary


async def ingest_skill_bundles_batch(
    *,
    sources: list[Path],
    config: Any,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    ingest_linked_papers: bool = True,
) -> list[IngestSummary]:
    """Ingest a list of skill bundle directories."""
    summaries: list[IngestSummary] = []
    for src in sources:
        summary = await ingest_skill_bundle(
            source=src,
            kb_name=None,
            config=config,
            vector_store=vector_store,
            embedding_service=embedding_service,
            session_store=session_store,
            ingest_linked_papers=ingest_linked_papers,
        )
        summaries.append(summary)
    return summaries
