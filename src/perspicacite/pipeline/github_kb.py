"""Top-level orchestrator for GitHub-repo / skill-bundle KB ingest.

This is the public surface of the 2026-05-15 GitHub / skill-bundle
ingest pipeline. Lower layers (``pipeline/github/fetcher.py``,
``pipeline/github/bundle.py``, ``pipeline/github/walk.py``,
``pipeline/github/chunk_producer.py``) do one job each; this module
wires them together against a KB target.

Three entry points:

* :func:`ingest_github_repo` — fetch a GitHub repo, walk + chunk, add
  Papers to a single KB. Never auto-routes linked papers (raw-repo
  mode; the user opts into chunked README only).
* :func:`ingest_skill_bundle` — parse the bundle.yml manifest (or fall
  back to README-only), walk + chunk, and OPTIONALLY route the
  manifest's deduplicated DOI list through
  :func:`perspicacite.pipeline.search_to_kb.ingest_dois_into_kb` so
  cited papers land in the same KB. ArXiv / PMC IDs surface in the
  summary as ``linked_papers_skipped_non_doi`` but aren't routed (v1
  routes DOIs only).
* :func:`ingest_skill_bundles_batch` — iterate a directory full of
  bundle subdirs. Per-skill mode produces one KB per bundle; composite
  mode (``composite_kb=<name>``) routes every bundle's chunks into the
  same KB, with ``source_skill`` chunk metadata differentiating them.

Design references:
- Spec: ``docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md``
- Plan: ``docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md`` Task 5
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from perspicacite.config.schema import Config
from perspicacite.models.kb import (
    ChunkConfig,
    KnowledgeBase,
    chroma_collection_name_for_kb,
)
from perspicacite.pipeline.github.bundle import (
    BundleManifest,
    ContentSpec,
)
from perspicacite.pipeline.github.chunk_producer import papers_from_directory
from perspicacite.pipeline.github.fetcher import (
    GitHubFetcher,
    parse_repo_url,
)
from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb
from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase

__all__ = [
    "IngestSummary",
    "ingest_github_repo",
    "ingest_skill_bundle",
    "ingest_skill_bundles_batch",
]

logger = logging.getLogger(__name__)


IngestMode = Literal["repo", "per-skill", "composite"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestSummary:
    """Per-target summary returned by the three orchestrators.

    Attributes
    ----------
    kb_name : str
        The KB that received the chunks. For composite-mode batch runs
        this is the same name across every summary.
    bundle_name : str | None
        ``manifest.name`` when ingesting a bundle; ``None`` for raw
        repos.
    repo_org, repo_name : str | None
        Owner + repo set only for the GitHub-repo path; ``None`` for
        local bundle sources.
    commit_sha : str | None
        Resolved commit SHA when the source is a GitHub URL; ``None``
        for local paths.
    files_added : int
        Count of files survived the include/exclude globs and produced
        a Paper. (Files that survive globs but read empty produce a
        Paper that the KB layer may drop — these still count here so
        operators see what the chunker tried.)
    chunks_added : int
        Count of vector-store rows actually written. Returned by
        :meth:`DynamicKnowledgeBase.add_papers`.
    linked_papers_added : int
        DOIs routed through :func:`ingest_dois_into_kb` that succeeded.
        Always ``0`` for the raw-repo path and when
        ``ingest_linked_papers=False``.
    linked_papers_skipped_non_doi : list[tuple[str, str]]
        Mined ``(kind, value)`` pairs that were NOT routed because v1
        only auto-resolves DOIs. ArXiv / PMC entries land here so the
        operator can route them manually.
    mode : "repo" | "per-skill" | "composite"
        Discriminates the call site so a single ``list[IngestSummary]``
        from the batch path stays self-describing.
    """

    kb_name: str
    bundle_name: str | None
    repo_org: str | None
    repo_name: str | None
    commit_sha: str | None
    files_added: int
    chunks_added: int
    linked_papers_added: int
    linked_papers_skipped_non_doi: list[tuple[str, str]]
    mode: IngestMode


# ---------------------------------------------------------------------------
# ingest_github_repo
# ---------------------------------------------------------------------------


async def ingest_github_repo(
    *,
    url: str,
    kb_name: str,
    config: Config,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    fetcher: GitHubFetcher | None = None,
    content: ContentSpec | None = None,
) -> IngestSummary:
    """Fetch a GitHub repo, walk + chunk it, write Papers into ``kb_name``.

    Args
    ----
    url : str
        GitHub URL. See :func:`parse_repo_url` for accepted shapes.
    kb_name : str
        Target KB. Created if it doesn't exist.
    config : Config
        Pipeline config. The ``github`` and ``knowledge_base`` blocks
        are consulted here; ``bundles`` is unused on the raw-repo path.
    vector_store, embedding_service, session_store : Any
        Storage seams. The orchestrator never reaches into AppState
        directly so unit/integration tests can pass deterministic
        fakes.
    fetcher : GitHubFetcher, optional
        Override for the fetcher. Tests pass a ``MagicMock(spec=...)``
        so no network is touched. In production this is constructed
        from ``config.github``.
    content : ContentSpec, optional
        Include/exclude globs for the walker. Defaults to the bundle
        defaults (markdown + python + notebooks + yaml).

    Notes
    -----
    *  Raw-repo mode never auto-routes linked papers — that's a
       bundle-only feature (the bundle author signed off on the
       citation list via ``papers:``; a random repo's mined DOIs are
       too noisy to trust).
    *  The synthetic manifest carries the org + repo on
       :attr:`BundleManifest.raw` so the chunk producer's ``Paper.id``
       prefix encodes the real repo coordinates rather than the
       placeholder ``bundle/<name>``.
    """
    ref = parse_repo_url(url)

    if fetcher is None:
        fetcher = _build_default_fetcher(config)

    root, sha = await fetcher.fetch(ref)
    walk_root = Path(root) / ref.subpath if ref.subpath else Path(root)

    # Synthetic manifest: the producer expects a BundleManifest for
    # author propagation + source_skill metadata. We bake org + repo
    # into ``raw`` so the chunk producer's id-builder picks them up.
    manifest = BundleManifest(
        name=f"{ref.org}__{ref.repo}",
        readme_only=False,
        raw={"org": ref.org, "repo": ref.repo},
        directory=walk_root,
    )

    papers = papers_from_directory(
        walk_root,
        manifest,
        commit_sha=sha,
        content=content,
    )

    chunks_added = await _add_papers_to_kb(
        kb_name=kb_name,
        papers=papers,
        config=config,
        vector_store=vector_store,
        embedding_service=embedding_service,
        session_store=session_store,
        description=f"GitHub repo ingest: {ref.org}/{ref.repo}@{sha}",
    )

    return IngestSummary(
        kb_name=kb_name,
        bundle_name=None,
        repo_org=ref.org,
        repo_name=ref.repo,
        commit_sha=sha,
        files_added=len(papers),
        chunks_added=chunks_added,
        linked_papers_added=0,
        linked_papers_skipped_non_doi=[],
        mode="repo",
    )


# ---------------------------------------------------------------------------
# ingest_skill_bundle
# ---------------------------------------------------------------------------


async def ingest_skill_bundle(
    *,
    source: Path | str,
    kb_name: str | None,
    config: Config,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    fetcher: GitHubFetcher | None = None,
    ingest_linked_papers: bool = True,
    app_state_for_doi_ingest: Any = None,
) -> IngestSummary:
    """Ingest one skill bundle into a KB.

    Args
    ----
    source : Path | str
        Local directory (``Path``) OR GitHub URL (``str``). Strings
        that don't look like a URL are coerced to ``Path``.
    kb_name : str | None
        Target KB. When ``None`` the orchestrator derives one from
        ``config.bundles.default_kb_name_template`` using the bundle's
        ``name``.
    ingest_linked_papers : bool
        When ``True`` (default), the manifest's deduplicated DOI list
        is routed through :func:`ingest_dois_into_kb`. ArXiv / PMC IDs
        are reported in ``linked_papers_skipped_non_doi`` but not
        routed (v1 only resolves DOIs).
    app_state_for_doi_ingest : Any
        Required when ``ingest_linked_papers=True`` because
        :func:`ingest_dois_into_kb` reads PDF-download config, the
        checkpoint store, and the vector store off it. The orchestrator
        deliberately keeps it separate from the explicit
        ``vector_store`` / ``session_store`` kwargs so the bundle-walk
        path stays usable with mocks alone.

    Raises
    ------
    ValueError
        If ``ingest_linked_papers=True`` but no ``app_state_for_doi_ingest``
        was supplied.
    """
    bundle_dir = await _resolve_bundle_source(source, config=config, fetcher=fetcher)
    manifest = BundleManifest.from_directory(bundle_dir)

    if kb_name is None:
        kb_name = config.bundles.default_kb_name_template.format(
            name=manifest.name,
        )

    if ingest_linked_papers and app_state_for_doi_ingest is None:
        raise ValueError(
            "app_state_for_doi_ingest is required when ingest_linked_papers=True"
        )

    papers = papers_from_directory(bundle_dir, manifest, commit_sha=None)

    chunks_added = await _add_papers_to_kb(
        kb_name=kb_name,
        papers=papers,
        config=config,
        vector_store=vector_store,
        embedding_service=embedding_service,
        session_store=session_store,
        description=(manifest.description or f"Skill bundle: {manifest.name}"),
    )

    linked_added, skipped_non_doi = await _route_linked_papers(
        manifest=manifest,
        kb_name=kb_name,
        ingest_linked_papers=ingest_linked_papers,
        app_state_for_doi_ingest=app_state_for_doi_ingest,
    )

    return IngestSummary(
        kb_name=kb_name,
        bundle_name=manifest.name,
        repo_org=None,
        repo_name=None,
        commit_sha=None,
        files_added=len(papers),
        chunks_added=chunks_added,
        linked_papers_added=linked_added,
        linked_papers_skipped_non_doi=skipped_non_doi,
        mode="per-skill",
    )


# ---------------------------------------------------------------------------
# ingest_skill_bundles_batch
# ---------------------------------------------------------------------------


async def ingest_skill_bundles_batch(
    *,
    root: Path,
    config: Config,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    composite_kb: str | None = None,
    ingest_linked_papers: bool = True,
    app_state_for_doi_ingest: Any = None,
) -> list[IngestSummary]:
    """Walk ``root`` and ingest each immediate subdir as a bundle.

    Composite mode (``composite_kb=<name>``) routes every bundle's
    chunks into the same KB; per-skill mode (default) creates one KB
    per bundle. Returns one :class:`IngestSummary` per discovered
    bundle in deterministic (sorted) order.

    Notes
    -----
    *  Order is deterministic so the resulting summary list survives
       golden-file comparisons in downstream tests.
    *  Composite mode rewrites each summary's ``mode`` to ``"composite"``
       so the caller can distinguish a single-bundle ingest from one of
       N composite shards without re-checking ``kb_name`` equality.
    """
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"batch root {root!r} is not a directory")

    summaries: list[IngestSummary] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue

        target_kb = composite_kb if composite_kb else None
        summary = await ingest_skill_bundle(
            source=sub,
            kb_name=target_kb,
            config=config,
            vector_store=vector_store,
            embedding_service=embedding_service,
            session_store=session_store,
            ingest_linked_papers=ingest_linked_papers,
            app_state_for_doi_ingest=app_state_for_doi_ingest,
        )
        if composite_kb:
            # Re-stamp mode + kb_name so the summary reads as "composite"
            # for downstream consumers without depending on the inner
            # call having known the composite context.
            summary = IngestSummary(
                kb_name=composite_kb,
                bundle_name=summary.bundle_name,
                repo_org=summary.repo_org,
                repo_name=summary.repo_name,
                commit_sha=summary.commit_sha,
                files_added=summary.files_added,
                chunks_added=summary.chunks_added,
                linked_papers_added=summary.linked_papers_added,
                linked_papers_skipped_non_doi=summary.linked_papers_skipped_non_doi,
                mode="composite",
            )
        summaries.append(summary)

    return summaries


# ---------------------------------------------------------------------------
# Internals — KB seam
# ---------------------------------------------------------------------------


async def _add_papers_to_kb(
    *,
    kb_name: str,
    papers: list,
    config: Config,
    vector_store: Any,
    embedding_service: Any,
    session_store: Any,
    description: str,
) -> int:
    """Create KB if missing, hydrate a DKB, add papers, update counts.

    Mirrors the pattern from
    :func:`perspicacite.pipeline.search_to_kb._create_kb_if_missing`
    plus :func:`ingest_dois_into_kb`. Lives here (not a module helper)
    so tests can intercept the orchestrator's behaviour without
    monkey-patching the underlying KB primitives.
    """
    collection_name = chroma_collection_name_for_kb(kb_name)

    kb_meta = await session_store.get_kb_metadata(kb_name)
    if kb_meta is None:
        await vector_store.create_collection(collection_name)
        chunk_cfg = ChunkConfig(
            chunk_size=config.knowledge_base.chunk_size,
            chunk_overlap=config.knowledge_base.chunk_overlap,
        )
        kb_meta = KnowledgeBase(
            name=kb_name,
            description=description,
            collection_name=collection_name,
            embedding_model=getattr(
                embedding_service, "model_name", "unknown"
            ),
            chunk_config=chunk_cfg,
        )
        await session_store.save_kb_metadata(kb_meta)

    if not papers:
        return 0

    dkb = DynamicKnowledgeBase(vector_store, embedding_service)
    dkb.collection_name = collection_name
    dkb._initialized = True
    added_chunks = await dkb.add_papers(papers, include_full_text=True)

    kb_meta.paper_count = (kb_meta.paper_count or 0) + len(papers)
    kb_meta.chunk_count = (kb_meta.chunk_count or 0) + added_chunks
    await session_store.save_kb_metadata(kb_meta)
    return added_chunks


# ---------------------------------------------------------------------------
# Internals — linked-paper routing
# ---------------------------------------------------------------------------


async def _route_linked_papers(
    *,
    manifest: BundleManifest,
    kb_name: str,
    ingest_linked_papers: bool,
    app_state_for_doi_ingest: Any,
) -> tuple[int, list[tuple[str, str]]]:
    """Resolve DOIs from the manifest and route them through the existing
    DOI-ingest pipeline. ArXiv / PMC references are reported back but
    not auto-routed (v1).
    """
    if not ingest_linked_papers:
        return 0, []

    refs = manifest.collect_paper_refs()
    dois = sorted({value for (kind, value) in refs if kind == "doi"})
    skipped: list[tuple[str, str]] = sorted(
        ((kind, value) for (kind, value) in refs if kind != "doi"),
        key=lambda kv: (kv[0], kv[1]),
    )

    if not dois:
        return 0, skipped

    try:
        result = await ingest_dois_into_kb(
            app_state_for_doi_ingest,
            kb_name=kb_name,
            dois=dois,
        )
    except Exception as exc:  # defensive: bundle ingest must still succeed
        logger.warning(
            "github_kb.linked_paper_ingest_failed",
            extra={"kb_name": kb_name, "error": str(exc)},
        )
        return 0, skipped

    if isinstance(result, dict):
        added = (
            result.get("added_papers")
            or result.get("added")
            or 0
        )
        try:
            added = int(added)
        except (TypeError, ValueError):
            added = 0
    else:
        added = 0
    return added, skipped


# ---------------------------------------------------------------------------
# Internals — fetcher + source resolution
# ---------------------------------------------------------------------------


def _build_default_fetcher(config: Config) -> GitHubFetcher:
    """Production-path fetcher constructor.

    The orchestrator builds this lazily so tests can pass a mock
    fetcher without paying for ``mkdir(cache_dir)`` on every test run.
    """
    import os

    token = os.environ.get(config.github.token_env_var)
    return GitHubFetcher(
        token=token,
        cache_dir=Path(config.github.cache_dir),
        cache_max_mb=config.github.cache_max_mb,
        user_agent=config.github.user_agent,
        api_base=config.github.api_base,
    )


async def _resolve_bundle_source(
    source: Path | str,
    *,
    config: Config,
    fetcher: GitHubFetcher | None,
) -> Path:
    """Return a local directory to walk.

    * ``Path`` → returned as-is.
    * ``str`` that parses as a GitHub URL → fetched (with the optional
      ``ref.subpath`` applied so a ``/tree/.../sub/bundle`` URL points
      at the bundle root, not the repo root).
    * Any other ``str`` → coerced to ``Path``.
    """
    if isinstance(source, Path):
        return source

    if isinstance(source, str) and source.startswith(("http://", "https://")):
        if fetcher is None:
            fetcher = _build_default_fetcher(config)
        ref = parse_repo_url(source)
        root, _sha = await fetcher.fetch(ref)
        return Path(root) / ref.subpath if ref.subpath else Path(root)

    return Path(source)
