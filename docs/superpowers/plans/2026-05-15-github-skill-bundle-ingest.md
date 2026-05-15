# GitHub + skill-bundle ingest — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship GitHub-repo + skill-bundle ingest per the design spec at `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`.

**Architecture:** Three layers — fetcher (`pipeline/github/fetcher.py`), bundle parser (`pipeline/github/bundle.py`), chunk producer (`pipeline/github/chunk_producer.py`) — orchestrated by `pipeline/github_kb.py`. Reuses `DynamicKnowledgeBase.add_papers` for storage and `ingest_dois_into_kb` for linked papers.

**Tech Stack:** httpx for GitHub REST, pyyaml for `bundle.yml`, the existing `Paper` / `Author` / `PaperSource` model, `rank_bm25` is unaffected, Wave 4.3 KB log for provenance.

---

### Task 1: Config knobs

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Test: `tests/unit/test_config_schema.py` (extend)

- [ ] **Step 1:** Add a `GitHubConfig` model and a `BundlesConfig` model. Wire them under `Config.github` and `Config.bundles`.

```python
class GitHubConfig(BaseModel):
    token_env_var: str = "GITHUB_TOKEN"
    cache_dir: Path = Path("data/github_cache")
    cache_max_mb: int = 2048
    default_branch: str = "HEAD"
    user_agent: str = "Perspicacite/2.0"
    api_base: str = "https://api.github.com"

class BundlesConfig(BaseModel):
    default_kb_name_template: str = "{name}"            # for per-skill mode
    composite_kb_name_template: str = "composite-{domain}"
```

- [ ] **Step 2:** Add `SearchFilters.source_skill: str | None` and plumb it through `MultiKBRetriever` and `DynamicKnowledgeBase.search`. The Wave 4.2 spec calls for `year_min`/`year_max`/`source`/`content_type` — `source_skill` slots in alongside.

- [ ] **Step 3:** Tests asserting defaults. Commit `feat(config): github + bundles config + source_skill filter`.

---

### Task 2: GitHub fetcher

**Files:**
- Create: `src/perspicacite/pipeline/github/__init__.py` (empty)
- Create: `src/perspicacite/pipeline/github/fetcher.py`
- Test: `tests/unit/test_github_fetcher.py`

- [ ] **Step 1:** Write failing tests for URL parsing.

```python
import pytest
from perspicacite.pipeline.github.fetcher import parse_repo_url, RepoRef

def test_basic_url():
    r = parse_repo_url("https://github.com/org/repo")
    assert r == RepoRef(org="org", repo="repo", ref=None, subpath=None)

def test_with_branch_via_at():
    r = parse_repo_url("https://github.com/org/repo@main")
    assert r.ref == "main"

def test_with_commit_sha_via_at():
    r = parse_repo_url("https://github.com/org/repo@abc1234")
    assert r.ref == "abc1234"

def test_with_tree_path():
    r = parse_repo_url("https://github.com/org/repo/tree/main/bundles/scrna-qc")
    assert r.ref == "main"
    assert r.subpath == "bundles/scrna-qc"

def test_bare_path_returns_none_subpath_when_no_tree():
    r = parse_repo_url("https://github.com/org/repo/blob/main/README.md")
    # blob URLs aren't directory targets — parse_repo_url should reject them.
    with pytest.raises(ValueError):
        parse_repo_url("https://github.com/org/repo/blob/main/README.md")
```

- [ ] **Step 2:** Implement `parse_repo_url`, `RepoRef`, and a `GitHubFetcher` class with:

```python
class GitHubFetcher:
    def __init__(self, *, token: str | None = None, cache_dir: Path,
                 user_agent: str = "Perspicacite/2.0",
                 api_base: str = "https://api.github.com"):
        ...

    async def resolve_commit_sha(self, ref: RepoRef) -> str:
        """GET /repos/{org}/{repo}/commits/{ref} -> sha"""

    async def fetch_tarball(self, ref: RepoRef, *, sha: str) -> Path:
        """Download via the tarball endpoint, extract to cache_dir/<sha>/.
        SHA cache hit returns the cached path without re-downloading."""

    async def fetch_clone(self, ref: RepoRef, *, sha: str) -> Path:
        """Shallow git clone fallback when tarball is rate-limited."""

    async def fetch(self, ref: RepoRef) -> tuple[Path, str]:
        """High-level: resolve SHA, hit cache, fall back to tarball,
        then clone. Returns (root_path, sha)."""
```

- [ ] **Step 3:** Mock-driven tests for `fetch_tarball` (httpx_mock for the API calls, tempfile for the extraction destination). Assert: cache hit on second call, `Authorization: Bearer <token>` header when token provided, `X-RateLimit-Reset`-aware retry/back-off.

- [ ] **Step 4:** Run `pytest tests/unit/test_github_fetcher.py -v`. All PASS.

- [ ] **Step 5:** Commit `feat(github): fetcher with tarball + clone fallback + SHA cache`.

---

### Task 3: Bundle manifest parser

**Files:**
- Create: `src/perspicacite/pipeline/github/bundle.py`
- Test: `tests/unit/test_bundle_manifest.py`

- [ ] **Step 1:** Write failing tests.

```python
def test_minimal_valid_yaml(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text("name: scrna-qc\n")
    m = BundleManifest.parse(p)
    assert m.name == "scrna-qc"
    assert m.papers == []
    assert m.content.include == DEFAULT_INCLUDE_GLOBS

def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text("name: x\nfuture_field: foo\n")
    m = BundleManifest.parse(p)        # no exception
    assert m.name == "x"

def test_falls_back_to_readme_when_yaml_missing(tmp_path):
    (tmp_path / "README.md").write_text("# My skill\n\nIntro.")
    m = BundleManifest.from_directory(tmp_path)
    assert m.name == tmp_path.name        # directory name
    assert m.readme_only is True

def test_link_extraction_from_papers_section(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text(
        "name: x\n"
        "papers:\n"
        "  - doi: 10.1234/foo\n"
        "  - arxiv: '2204.12345'\n"
        "  - pmc: 'PMC9123456'\n"
    )
    m = BundleManifest.parse(p)
    dois = m.collect_paper_refs()
    assert ("doi", "10.1234/foo") in dois
    assert ("arxiv", "2204.12345") in dois
    assert ("pmc", "PMC9123456") in dois
```

- [ ] **Step 2:** Implement the dataclass + parser + the regex-based extractor for inline links in README / docs (separate function `extract_links_from_text(text)` returning a `LinkBag`).

- [ ] **Step 3:** Run tests. PASS.

- [ ] **Step 4:** Commit `feat(github): bundle.yml parser + link extractor`.

---

### Task 4: File walker + chunk producer

**Files:**
- Create: `src/perspicacite/pipeline/github/walk.py`
- Create: `src/perspicacite/pipeline/github/chunk_producer.py`
- Test: `tests/unit/test_github_chunk_producer.py` + `test_github_walk.py`

- [ ] **Step 1:** Tests first — assert that a fixture directory under `tests/data/sample_bundle/` produces:

```python
def test_walker_respects_include_exclude(tmp_path):
    ...
def test_chunk_producer_emits_markdown_paper(tmp_path):
    # README.md → Paper with content_type="docs"
    ...
def test_chunk_producer_handles_notebook(tmp_path):
    # .ipynb → strip cells, drop large outputs, emit single Paper
    ...
def test_chunk_producer_extracts_docstrings(tmp_path):
    # .py → emit one Paper per module containing only docstring + signatures
    # (NOT the full source body for v1)
    ...
def test_links_in_readme_attached_to_paper_metadata(tmp_path):
    # README contains "see 10.1234/foo" → Paper's metadata carries
    # `mined_dois=["10.1234/foo"]`
    ...
```

- [ ] **Step 2:** Implement `walk_filtered(root, include, exclude)` using `pathspec` library (already a dep of any modern PyData project — confirm with `pip show pathspec`; if not, add to `pyproject.toml`).

- [ ] **Step 3:** Implement `chunk_producer.papers_from_directory(root, manifest, commit_sha)` returning `list[Paper]`. Use the existing chunker downstream; this function only builds Paper fixtures.

- [ ] **Step 4:** Notebook stripping via `nbformat` (already a transitive dep). Cells joined with `\n\n# Cell N\n\n`. Large image / base64 outputs dropped.

- [ ] **Step 5:** Python docstring extraction via `ast.parse` — top-level + class + function docstrings only. Full source bodies are out-of-scope for v1 (avoids embedding noise).

- [ ] **Step 6:** Commit `feat(github): file walker + chunk producer (md/py/ipynb)`.

---

### Task 5: Top-level orchestrator + linked-paper ingest

**Files:**
- Create: `src/perspicacite/pipeline/github_kb.py`
- Test: `tests/integration/test_github_kb_e2e.py`

- [ ] **Step 1:** Fixture: build `tests/data/sample_bundle/` with:
  - `bundle.yml` (4 papers, 1 dataset, 1 tool link).
  - `README.md` mentioning 2 more DOIs inline.
  - `docs/intro.md`.
  - `notebooks/qc.ipynb` (2 markdown cells, 1 code cell).
  - `src/qc.py` with module docstring + 2 function docstrings.

- [ ] **Step 2:** Write the e2e integration test (mocked GitHub fetcher when a URL is used; real filesystem when a path is used):

```python
@pytest.mark.asyncio
async def test_ingest_skill_bundle_per_skill_mode(tmp_path, deterministic_embedder, monkeypatch):
    # Monkeypatch ingest_dois_into_kb to record calls without HTTP.
    captured = []
    async def fake_ingest(*, dois, **kw):
        captured.append(dois)
        return {"added": len(dois), "skipped": 0, "failed": 0}
    monkeypatch.setattr(
        "perspicacite.pipeline.github_kb.ingest_dois_into_kb", fake_ingest
    )
    summary = await ingest_skill_bundle(
        source=Path("tests/data/sample_bundle/"),
        kb_name=None,                # use bundle.yml's "name"
        config=cfg,
        vector_store=vs,
        embedding_service=deterministic_embedder,
        session_store=ss,
        ingest_linked_papers=True,
    )
    assert summary.bundle_name is not None
    assert summary.files_added >= 3
    assert summary.chunks_added > 0
    assert summary.linked_papers_added == 6  # 4 from yaml + 2 from README
    assert captured  # ingest_dois_into_kb was called
```

- [ ] **Step 3:** Implement `ingest_github_repo`, `ingest_skill_bundle`, and `ingest_skill_bundles_batch`. Reuse `DynamicKnowledgeBase.add_papers` for storage. Linked-paper ingest re-uses `ingest_dois_into_kb` — pass `source_command="ingest_skill_bundle"` so KB-log entries are tagged correctly.

- [ ] **Step 4:** Run tests; iterate.

- [ ] **Step 5:** Commit `feat(github): top-level ingest_github_repo + ingest_skill_bundle + batch`.

---

### Task 6: CLI wiring

**Files:**
- Modify: `src/perspicacite/cli.py`
- Test: smoke run from the shell

- [ ] **Step 1:** Add three click commands: `ingest-github-repo`, `ingest-skill-bundle`, `ingest-skill-bundles`. Mirror the existing `ingest-local` shape.

- [ ] **Step 2:** Document each command's `--help` output in a comment.

- [ ] **Step 3:** Run `perspicacite ingest-skill-bundle tests/data/sample_bundle/ --kb test_bundle_kb --no-linked-papers` (no network) and verify a KB is created with chunks.

- [ ] **Step 4:** Commit `feat(cli): ingest-github-repo + ingest-skill-bundle[s] commands`.

---

### Task 7: MCP wiring

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/integration/test_mcp_smoke.py` (extend) + `tests/unit/test_mcp_github_tools.py`

- [ ] **Step 1:** Add `@mcp.tool() ingest_github_repo(...)` and `@mcp.tool() ingest_skill_bundle(...)` wrappers that call into `pipeline.github_kb`.

- [ ] **Step 2:** Make sure the existing MCP-tool-inventory smoke test (Wave 1.3) discovers them and they invoke with minimal valid args.

- [ ] **Step 3:** Commit `feat(mcp): ingest_github_repo + ingest_skill_bundle tools`.

---

### Task 8: Operator doc

**Files:**
- Create: `docs/github-skill-bundle-ingest-2026-05-15.md`
- Modify: `.gitignore` (allowlist `!docs/github-skill-bundle-*.md`)

- [ ] **Step 1:** Write the operator guide following the spec's outline (~150 lines).

- [ ] **Step 2:** Commit `docs(github-bundles): operator guide`.

---

### Task 9: Wire the link-extractor warnings into KB log

**Files:**
- Modify: `src/perspicacite/pipeline/github_kb.py`

- [ ] **Step 1:** When `metadata_only_links` is non-empty, emit one Wave 4.3 KB-log event per link (`event="external_link"`, `source_command="ingest_skill_bundle"`, `url=<the url>`).

- [ ] **Step 2:** Test asserting the events land in the log.

- [ ] **Step 3:** Commit `feat(github): emit external_link KB-log events for non-paper URLs`.

---

### Task 10: Final integration test + checklist

- [ ] **Step 1:** Run the full Wave 6 suite to ensure nothing regressed:

```bash
pytest tests/unit/ tests/e2e/ tests/integration/ -m "not live" -v
```

- [ ] **Step 2:** Cap stoned: add a `## Followups (post-v1)` section to the operator doc listing the followups from the design spec (code-symbol indexing, notebook execution, GitLab adapter, etc.).

- [ ] **Step 3:** Final commit `docs(roadmap): GitHub + skill-bundle ingest shipped (2026-05-15)`.
