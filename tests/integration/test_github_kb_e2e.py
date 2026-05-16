"""End-to-end integration tests for ``perspicacite.pipeline.github_kb``.

Covers the top-level orchestrator entry points introduced by Task 5 of
the 2026-05-15 GitHub / skill-bundle ingest plan:

* ``ingest_github_repo`` — fetch a repo, walk it, add Papers to a KB.
* ``ingest_skill_bundle`` — parse bundle.yml, walk + chunk, optionally
  route linked DOIs through ``ingest_dois_into_kb``.
* ``ingest_skill_bundles_batch`` — repeat per subdir (per-skill mode)
  or aggregate into a single composite KB.

Both ``ingest_dois_into_kb`` and the ``GitHubFetcher`` are seam-points
patched here so the tests stay fully offline. Real Chroma is used via
the top-level ``deterministic_embedder`` fixture (defined in
``tests/conftest.py``) so KB writes survive the round trip.

See:
- Spec: ``docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md``
- Plan: ``docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md`` Task 5
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Skip whole file cleanly if Chroma isn't available. We importorskip
# *before* importing the orchestrator so a Chroma-less environment
# short-circuits cleanly rather than failing inside dynamic_kb's
# transitive imports.
chromadb = pytest.importorskip("chromadb")

from perspicacite.config.schema import Config
from perspicacite.pipeline.github.fetcher import GitHubFetcher


SAMPLE_BUNDLE = Path(__file__).parent.parent / "data" / "sample_bundle"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chroma_store(tmp_path: Path, embedder):
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    return ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=embedder,
    )


async def _make_session_store(tmp_path: Path):
    from perspicacite.memory.session_store import SessionStore

    store = SessionStore(db_path=str(tmp_path / "session.db"))
    await store.init_db()
    return store


def _make_config() -> Config:
    """A Config with defaults. The orchestrator only touches the
    ``bundles`` and ``knowledge_base`` sub-configs in v1."""
    return Config()


# ---------------------------------------------------------------------------
# ingest_skill_bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_skill_bundle_per_skill_mode(
    tmp_path: Path, deterministic_embedder, monkeypatch
):
    """A local bundle path → per-skill KB created, files chunked + added,
    linked DOIs routed through ingest_dois_into_kb."""
    from perspicacite.pipeline import github_kb

    captured_calls: list[dict] = []

    async def fake_ingest_dois(app_state, kb_name, dois, **kw):  # noqa: ARG001
        captured_calls.append({"kb_name": kb_name, "dois": list(dois), "kw": kw})
        return {
            "added_papers": len(dois),
            "added_chunks": len(dois),
            "skipped_duplicates": 0,
            "failed": [],
        }

    monkeypatch.setattr(github_kb, "ingest_dois_into_kb", fake_ingest_dois)

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summary = await github_kb.ingest_skill_bundle(
        source=SAMPLE_BUNDLE,
        kb_name="test_bundle_kb",
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        ingest_linked_papers=True,
        app_state_for_doi_ingest=MagicMock(),  # only forwarded to the patched ingest_dois
    )

    assert summary.bundle_name == "sample-bundle"
    assert summary.kb_name == "test_bundle_kb"
    assert summary.mode == "per-skill"
    # README + intro + qc.ipynb + qc.py = 4 papers; the producer also
    # ingests bundle.yml under default globs (yaml/yml). Spec asks for >= 3.
    assert summary.files_added >= 3
    assert summary.chunks_added > 0
    # 4 DOIs survive routing: 2 YAML DOIs + 2 README inline DOIs.
    assert summary.linked_papers_added == 4
    # arxiv + PMC mentioned but not routed (v1 only routes DOIs).
    skipped_kinds = {kind for kind, _ in summary.linked_papers_skipped_non_doi}
    assert skipped_kinds == {"arxiv", "pmc"}

    # Exactly one ingest_dois_into_kb call, targeting our KB, with the
    # exact dedup'd DOI set.
    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["kb_name"] == "test_bundle_kb"
    assert set(call["dois"]) == {
        "10.1234/yaml-paper-1",
        "10.1234/yaml-paper-2",
        "10.5678/readme-paper-1",
        "10.5678/readme-paper-2",
    }


@pytest.mark.asyncio
async def test_ingest_skill_bundle_without_linked_papers(
    tmp_path: Path, deterministic_embedder, monkeypatch
):
    """ingest_linked_papers=False → ingest_dois_into_kb is NOT called."""
    from perspicacite.pipeline import github_kb

    fake = AsyncMock()
    monkeypatch.setattr(github_kb, "ingest_dois_into_kb", fake)

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summary = await github_kb.ingest_skill_bundle(
        source=SAMPLE_BUNDLE,
        kb_name="no_links_kb",
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        ingest_linked_papers=False,
    )
    assert summary.linked_papers_added == 0
    assert summary.linked_papers_skipped_non_doi == []
    fake.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_skill_bundle_kb_name_from_template(
    tmp_path: Path, deterministic_embedder, monkeypatch
):
    """When kb_name is None, derive from bundles.default_kb_name_template."""
    from perspicacite.pipeline import github_kb

    async def fake_ingest_dois(*a, **kw):  # noqa: ARG001
        return {
            "added_papers": 0,
            "added_chunks": 0,
            "skipped_duplicates": 0,
            "failed": [],
        }

    monkeypatch.setattr(github_kb, "ingest_dois_into_kb", fake_ingest_dois)

    cfg = _make_config()
    cfg.bundles.default_kb_name_template = "kb-from-{name}"
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summary = await github_kb.ingest_skill_bundle(
        source=SAMPLE_BUNDLE,
        kb_name=None,
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        ingest_linked_papers=False,
    )
    # bundle name is "sample-bundle".
    assert summary.kb_name == "kb-from-sample-bundle"


@pytest.mark.asyncio
async def test_ingest_skill_bundle_requires_app_state_when_linking_papers(
    tmp_path: Path, deterministic_embedder
):
    """ingest_linked_papers=True without app_state_for_doi_ingest → ValueError."""
    from perspicacite.pipeline import github_kb

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    with pytest.raises(ValueError, match="app_state_for_doi_ingest"):
        await github_kb.ingest_skill_bundle(
            source=SAMPLE_BUNDLE,
            kb_name="x",
            config=cfg,
            vector_store=vs,
            embedding_service=deterministic_embedder,
            session_store=ss,
            ingest_linked_papers=True,
            app_state_for_doi_ingest=None,
        )


# ---------------------------------------------------------------------------
# ingest_github_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_github_repo_minimal(
    tmp_path: Path, deterministic_embedder
):
    """Mock the GitHubFetcher to point at the sample_bundle dir; assert
    the orchestrator wires SHA + repo metadata into the summary."""
    from perspicacite.pipeline import github_kb

    fake_fetcher = MagicMock(spec=GitHubFetcher)
    fake_fetcher.fetch = AsyncMock(
        return_value=(SAMPLE_BUNDLE, "abcdef1234567890")
    )

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summary = await github_kb.ingest_github_repo(
        url="https://github.com/example/sample-bundle",
        kb_name="repo_kb",
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        fetcher=fake_fetcher,
    )

    assert summary.kb_name == "repo_kb"
    assert summary.bundle_name is None  # raw-repo mode
    assert summary.mode == "repo"
    assert summary.repo_org == "example"
    assert summary.repo_name == "sample-bundle"
    assert summary.commit_sha == "abcdef1234567890"
    assert summary.files_added >= 3
    assert summary.chunks_added > 0
    # ingest_github_repo never auto-routes linked papers (per spec).
    assert summary.linked_papers_added == 0
    assert summary.linked_papers_skipped_non_doi == []
    fake_fetcher.fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# ingest_skill_bundles_batch
# ---------------------------------------------------------------------------


def _materialise_two_bundles(root: Path) -> None:
    """Stamp two minimal bundles under ``root`` for the batch tests."""
    for name in ("bundle-a", "bundle-b"):
        sub = root / name
        sub.mkdir(parents=True, exist_ok=True)
        # Each bundle gets a unique YAML name so the per-skill KBs differ.
        (sub / "bundle.yml").write_text(
            f"name: {name}\n"
            "papers:\n"
            f"  - doi: '10.1234/{name}-paper'\n"
        )
        (sub / "README.md").write_text(f"# {name}\n\nIntro.\n")


@pytest.mark.asyncio
async def test_ingest_skill_bundles_batch_per_skill_mode(
    tmp_path: Path, deterministic_embedder, monkeypatch
):
    from perspicacite.pipeline import github_kb

    root = tmp_path / "bundles_root"
    _materialise_two_bundles(root)

    async def fake_ingest_dois(*a, **kw):  # noqa: ARG001
        return {
            "added_papers": 0,
            "added_chunks": 0,
            "skipped_duplicates": 0,
            "failed": [],
        }

    monkeypatch.setattr(github_kb, "ingest_dois_into_kb", fake_ingest_dois)

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summaries = await github_kb.ingest_skill_bundles_batch(
        root=root,
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        composite_kb=None,
        ingest_linked_papers=False,
    )
    assert len(summaries) == 2
    kb_names = sorted(s.kb_name for s in summaries)
    assert kb_names == ["bundle-a", "bundle-b"]
    for s in summaries:
        assert s.mode == "per-skill"
        assert s.bundle_name in {"bundle-a", "bundle-b"}


@pytest.mark.asyncio
async def test_ingest_skill_bundles_batch_composite_mode(
    tmp_path: Path, deterministic_embedder, monkeypatch
):
    from perspicacite.pipeline import github_kb

    root = tmp_path / "bundles_root"
    _materialise_two_bundles(root)

    async def fake_ingest_dois(*a, **kw):  # noqa: ARG001
        return {
            "added_papers": 0,
            "added_chunks": 0,
            "skipped_duplicates": 0,
            "failed": [],
        }

    monkeypatch.setattr(github_kb, "ingest_dois_into_kb", fake_ingest_dois)

    cfg = _make_config()
    vs = _make_chroma_store(tmp_path, deterministic_embedder)
    ss = await _make_session_store(tmp_path)

    summaries = await github_kb.ingest_skill_bundles_batch(
        root=root,
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        composite_kb="my-composite",
        ingest_linked_papers=False,
    )
    assert len(summaries) == 2
    for s in summaries:
        assert s.kb_name == "my-composite"
        assert s.mode == "composite"
