# ASB Bundle Ingest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest Agent Skill Bundle (ASB) run outputs into Perspicacité KBs per [`docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md`](../specs/2026-05-15-asb-bundle-ingest-design.md). Covers skills + workflow cards + `workflow_dag.json` + `skill_kb.json` round-trip. Repo-fetching (cloning github URLs from `links.json`) is intentionally deferred to the parent [`2026-05-15-github-skill-bundle-ingest.md`](2026-05-15-github-skill-bundle-ingest.md) plan and not required for this plan to ship; v1 indexes skill bodies + workflow cards + linked-paper DOIs only.

**Architecture:** Three parsers (skill, card, dag) feed a shared chunk producer; orchestrator (`pipeline/asb/run_ingest.py`) chooses composite vs. per-skill KB mode, runs the existing `ingest_dois_into_kb` for backing papers, and routes parsed records → Papers → `DynamicKnowledgeBase.add_papers`. The per-chunk metadata schema piggybacks on `Paper.metadata: dict[str, Any]`; existing chunker passes it through. `skill_kb.json` round-trip is in-place JSON edit. MCP tool `ingest_asb_run` + CLI `ingest-asb-run` are thin wrappers. Response-layer `skill_metadata` + `workflow_metadata` blocks are surfaced in the chat-router response when chunks carry the relevant metadata.

**Tech Stack:** Python 3.11+, pydantic v2 for parsed-record models, `pyyaml` for `skill.md` frontmatter, `httpx` async only for the existing DOI ingest path, the existing `Paper`/`Author`/`PaperSource` model + new `PaperSource.SKILL_BUNDLE` enum value, the existing `TypedEmbeddingProvider` for chunk embedding.

---

## File structure (new modules)

```
src/perspicacite/pipeline/asb/
├── __init__.py
├── models.py                # pydantic models for parsed ASB records
├── skill_parser.py          # parses skills/_index.json + skills/{slug}/
├── card_parser.py           # parses cards/task_NNN.{md,json}
├── dag.py                   # parses workflow_dag.json + upstream/downstream maps
├── chunk_producer.py        # ParsedSkill | ParsedCard → list[Paper] with metadata
├── skill_kb_writer.py       # in-place skill_kb.json round-trip
└── run_ingest.py            # top-level orchestrator

tests/fixtures/asb/metlinkr_subset/
├── skills/
│   ├── _index.json                                       # 1 skill catalog entry
│   └── cross-identifier-reconciliation/                  # 1 skill bundle
│       ├── skill.md, README.md, tools.json, environments.json,
│       ├── parameters.json, papers.json, links.json,
│       ├── ontology_refs.json, examples.jsonl, failure_modes.jsonl,
│       ├── artifact_provenance.json, skill_kb.json
├── cards/
│   ├── task_001.md, task_001.json
│   └── task_002.md, task_002.json
├── tools/                                                # registry; 2 tools
│   ├── _index.json
│   ├── metlinkr.json
│   └── r.json
└── workflow_dag.json

tests/unit/test_asb_skill_parser.py
tests/unit/test_asb_card_parser.py
tests/unit/test_asb_dag.py
tests/unit/test_asb_chunk_producer.py
tests/unit/test_asb_skill_kb_writer.py
tests/unit/test_asb_response_metadata.py
tests/integration/test_asb_run_ingest_end_to_end.py
```

Existing modules touched:
- `src/perspicacite/models/papers.py` — add `PaperSource.SKILL_BUNDLE`
- `src/perspicacite/mcp/server.py` — add `ingest_asb_run` MCP tool (around the existing skill ingest tools)
- `src/perspicacite/cli.py` (or wherever the CLI commands live) — add `ingest-asb-run` command
- `src/perspicacite/web/routers/chat.py` — extend response payload with `skill_metadata` + `workflow_metadata`
- `tests/unit/test_paper_source_no_websearch_defaults.py` — extend file-wide invariant (allowance for `SKILL_BUNDLE`)
- `tests/unit/test_paper_source_enum.py` — extend enum-existence assertions

---

## Standing notes for the implementer

- **PYTHONPATH=src** when running pytest inside this worktree. Editable install at the main-repo level points to the main repo's `src/`; without `PYTHONPATH=src` you'll silently test the wrong tree.
- **Real fixture, not synthetic.** Tasks reference real files at `~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/`. Copy verbatim to keep the parser honest against schema drift.
- **TDD.** Each task: write the failing test → run to confirm fail → minimal impl → run to confirm pass → commit.
- **Per-task commit directly to the worktree branch.** Don't push.
- **`Paper.metadata` is the metadata seam.** The chunker reads from `paper.metadata` and propagates onto each chunk's `ChunkMetadata`. We do NOT need a new chunker — Paper-construction sites stash skill/card metadata in `paper.metadata`, and existing infrastructure carries it through.

---

### Task 1: Add `PaperSource.SKILL_BUNDLE` enum

**Files:**
- Modify: `src/perspicacite/models/papers.py:10` (PaperSource enum)
- Modify: `tests/unit/test_paper_source_enum.py`
- Modify: `tests/unit/test_paper_source_no_websearch_defaults.py` (allowance list)

- [ ] **Step 1: Write the failing enum test**

```python
# tests/unit/test_paper_source_enum.py — add to existing test
def test_skill_bundle_enum_value_exists():
    """SKILL_BUNDLE was added 2026-05-15 for ASB ingest. Pin the value."""
    from perspicacite.models.papers import PaperSource
    assert PaperSource.SKILL_BUNDLE.value == "skill_bundle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_paper_source_enum.py::test_skill_bundle_enum_value_exists -v`
Expected: `FAIL — AttributeError: SKILL_BUNDLE`

- [ ] **Step 3: Add the enum value**

```python
# src/perspicacite/models/papers.py
class PaperSource(str, Enum):
    """...existing docstring...

    The 2026-05-15 ASB-bundle-ingest plan added SKILL_BUNDLE for
    chunks sourced from ASB skill bundles or workflow cards.
    """

    BIBTEX = "bibtex"
    # ... existing values ...
    SEMANTIC_SCHOLAR = "semantic_scholar"
    SKILL_BUNDLE = "skill_bundle"
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_paper_source_enum.py -v`
Expected: `PASS` on the new test (plus existing).

- [ ] **Step 5: Extend the file-wide WEB_SEARCH invariant test**

The pin test `tests/unit/test_paper_source_no_websearch_defaults.py` walks `src/perspicacite/` for `source=PaperSource.WEB_SEARCH` strings. It uses an `ALLOWED_FILES` allowance list. Adding `SKILL_BUNDLE` does NOT touch WEB_SEARCH at all — but ensure the test's docstring acknowledges that new sources (like SKILL_BUNDLE for skill-bundle ingest) are added the same way the 2026-05-15 migration did. No code change beyond a docstring touch.

- [ ] **Step 6: Run the full PaperSource test slice**

Run: `PYTHONPATH=src pytest tests/unit/test_paper_source_enum.py tests/unit/test_paper_source_no_websearch_defaults.py tests/unit/test_paper_source_adapter_migration.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/models/papers.py tests/unit/test_paper_source_enum.py tests/unit/test_paper_source_no_websearch_defaults.py
git commit -m "feat(models): add PaperSource.SKILL_BUNDLE for ASB bundle ingest"
```

---

### Task 2: Copy the MetLinkR ASB fixture into the test tree

**Files:**
- Create: `tests/fixtures/asb/metlinkr_subset/` (directory tree — see File-structure block at top)

- [ ] **Step 1: Verify the source path exists**

Run: `ls ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/ ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/cards/ ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/workflow_dag.json`
Expected: all paths listed; `task_001.md`, `task_001.json`, `task_002.md`, `task_002.json` exist under `cards/`.

- [ ] **Step 2: Copy the subset**

```bash
mkdir -p tests/fixtures/asb/metlinkr_subset/{skills/cross-identifier-reconciliation,cards,tools}

# One skill
cp -r ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/* tests/fixtures/asb/metlinkr_subset/skills/cross-identifier-reconciliation/

# The skill index (will be edited to one entry below)
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/_index.json tests/fixtures/asb/metlinkr_subset/skills/_index.json

# Two cards
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/cards/task_00{1,2}.* tests/fixtures/asb/metlinkr_subset/cards/

# Two tools + tool index
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/tools/{metlinkr.json,r.json,_index.json} tests/fixtures/asb/metlinkr_subset/tools/

# The DAG
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/workflow_dag.json tests/fixtures/asb/metlinkr_subset/workflow_dag.json
```

- [ ] **Step 3: Trim `skills/_index.json` to one skill entry**

Open `tests/fixtures/asb/metlinkr_subset/skills/_index.json`. The full file lists 28 skills. Replace with a single-skill version pinned to `cross-identifier-reconciliation`:

```json
{
  "skills": [
    {
      "slug": "cross-identifier-reconciliation",
      "name": "cross-identifier-reconciliation",
      "description": "Cross-identifier reconciliation maps multiple per-metabolite identifiers (HMDB, KEGG, PubChem, common name, LIPID MAPS, ChEBI) to a single RefMet standardized name and flags rows where those identifiers resolve to conflicting names...",
      "edam_operation": "http://edamontology.org/operation_0224",
      "schema_version": "0.2.0",
      "body_path": "skills/cross-identifier-reconciliation/skill.md"
    }
  ]
}
```

(Keep the real description and edam_operation; copying-trimming-saving is sufficient. The trim keeps fixture tests focused on one skill.)

- [ ] **Step 4: Confirm fixture parses as JSON**

```bash
python3 -c "import json; json.load(open('tests/fixtures/asb/metlinkr_subset/skills/_index.json'))"
python3 -c "import json; json.load(open('tests/fixtures/asb/metlinkr_subset/cards/task_001.json'))"
python3 -c "import json; json.load(open('tests/fixtures/asb/metlinkr_subset/workflow_dag.json'))"
```
Expected: no output (silent success).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/asb/
git commit -m "test(asb): copy MetLinkR subset fixture (1 skill, 2 cards, DAG)"
```

---

### Task 3: ASB skill parser

**Files:**
- Create: `src/perspicacite/pipeline/asb/__init__.py` (empty)
- Create: `src/perspicacite/pipeline/asb/models.py`
- Create: `src/perspicacite/pipeline/asb/skill_parser.py`
- Test: `tests/unit/test_asb_skill_parser.py`

- [ ] **Step 1: Write the failing parser test (asserts top-level shape)**

```python
# tests/unit/test_asb_skill_parser.py
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_parse_skill_bundle_finds_one_skill():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skills = parse_skill_bundle(FIXTURE)
    assert len(skills) == 1
    assert skills[0].slug == "cross-identifier-reconciliation"
    assert "metabolite" in skills[0].description.lower()


def test_parse_skill_bundle_extracts_tools():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    tool_names = {t.name for t in skill.tools}
    assert "MetLinkR" in tool_names


def test_parse_skill_bundle_extracts_parameters_and_environments():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    assert isinstance(skill.parameters, list)
    assert isinstance(skill.environments, list)
    assert any(env.language for env in skill.environments)  # at least one env named


def test_parse_skill_bundle_loads_papers_and_links():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    # MetLinkR has at least one backing paper DOI
    dois = [p.doi for p in skill.papers if p.doi]
    assert any(doi.startswith("10.1021/") for doi in dois)
    # links.json has at least one entry
    assert isinstance(skill.links, list)


def test_parse_skill_bundle_includes_body_text():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    assert skill.body_markdown  # non-empty
    assert "RefMet" in skill.body_markdown or "metabolite" in skill.body_markdown.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_skill_parser.py -v`
Expected: `ImportError: No module named perspicacite.pipeline.asb.skill_parser`

- [ ] **Step 3: Define the parsed-record models**

```python
# src/perspicacite/pipeline/asb/models.py
"""Pydantic models for parsed ASB artifacts.

These are *parsed* records — pure data, no behavior. The chunk
producer converts them into Paper objects with metadata.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParsedTool(BaseModel):
    """One tool record (from tools.json or tools/{slug}.json registry)."""
    model_config = ConfigDict(extra="allow")  # ASB schema may evolve

    slug: str | None = None
    name: str
    canonical_url: str | None = None
    install: str | None = None
    role: str | None = None  # frontmatter-only field
    related_skills: list[str] = Field(default_factory=list)
    source_task_ids: list[str] = Field(default_factory=list)
    source_paper_doi: str | None = None
    source_paper_title: str | None = None
    evidence_spans: list[str] = Field(default_factory=list)


class ParsedEnvironment(BaseModel):
    model_config = ConfigDict(extra="allow")
    language: str | None = None
    version: str | None = None
    packages: list[str] = Field(default_factory=list)
    dockerfile_hint: str | None = None


class ParsedParameter(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str | None = None
    typical: str | None = None
    min: Any | None = None
    max: Any | None = None
    units: str | None = None
    source_citation: str | None = None
    source_doi: str | None = None


class ParsedPaperRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    role: str | None = None


class ParsedLink(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    category: str
    source: str | None = None
    surrounding_text: str | None = None


class ParsedSkill(BaseModel):
    """Result of parsing one skills/{slug}/ directory."""
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    description: str
    edam_operation: str | None = None
    edam_topics: list[str] = Field(default_factory=list)
    when_to_use_negative: list[str] = Field(default_factory=list)
    schema_version: str | None = None

    body_markdown: str = ""           # skill.md body (post-frontmatter)
    tools: list[ParsedTool] = Field(default_factory=list)
    environments: list[ParsedEnvironment] = Field(default_factory=list)
    parameters: list[ParsedParameter] = Field(default_factory=list)
    papers: list[ParsedPaperRef] = Field(default_factory=list)
    links: list[ParsedLink] = Field(default_factory=list)
    asb_task_ids: list[str] = Field(default_factory=list)

    bundle_dir: str = ""              # relative path under the run dir
```

- [ ] **Step 4: Write the parser**

```python
# src/perspicacite/pipeline/asb/skill_parser.py
"""Parser for ASB skill bundles.

Reads {run_dir}/skills/_index.json and walks each per-skill
directory, returning a list[ParsedSkill] for downstream conversion
to Papers.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from perspicacite.pipeline.asb.models import (
    ParsedEnvironment,
    ParsedLink,
    ParsedPaperRef,
    ParsedParameter,
    ParsedSkill,
    ParsedTool,
)

logger = logging.getLogger(__name__)


def parse_skill_bundle(run_dir: Path | str) -> list[ParsedSkill]:
    """Walk an ASB run directory and return one ParsedSkill per
    entry in skills/_index.json. Missing sidecar files yield empty
    fields rather than errors."""
    run_dir = Path(run_dir)
    index_path = run_dir / "skills" / "_index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"ASB skills index not found at {index_path}")
    index = json.loads(index_path.read_text())
    out: list[ParsedSkill] = []
    for entry in index.get("skills", []):
        slug = entry["slug"]
        skill_dir = run_dir / "skills" / slug
        if not skill_dir.is_dir():
            logger.warning("asb_skill_missing", slug=slug)
            continue
        out.append(_parse_one_skill(skill_dir=skill_dir, index_entry=entry))
    return out


def _parse_one_skill(*, skill_dir: Path, index_entry: dict) -> ParsedSkill:
    slug = index_entry["slug"]

    # 1. skill.md → frontmatter + body
    frontmatter, body = _split_frontmatter(skill_dir / "skill.md")

    # 2. JSON sidecars (all optional)
    tools_raw = _load_json(skill_dir / "tools.json", default={"tools": []})
    envs_raw = _load_json(skill_dir / "environments.json", default=[])
    params_raw = _load_json(skill_dir / "parameters.json", default=[])
    papers_raw = _load_json(skill_dir / "papers.json", default=[])
    links_raw = _load_json(skill_dir / "links.json", default=[])
    provenance_raw = _load_json(skill_dir / "artifact_provenance.json", default={})

    return ParsedSkill(
        slug=slug,
        name=index_entry.get("name", slug),
        description=index_entry.get("description", frontmatter.get("description", "")),
        edam_operation=index_entry.get("edam_operation") or frontmatter.get("edam_operation"),
        edam_topics=frontmatter.get("edam_topics", []) or [],
        when_to_use_negative=frontmatter.get("when_to_use_negative", []) or [],
        schema_version=index_entry.get("schema_version") or frontmatter.get("schema_version"),
        body_markdown=body,
        tools=[ParsedTool(**t) for t in tools_raw.get("tools", [])],
        environments=[ParsedEnvironment(**e) for e in envs_raw],
        parameters=[ParsedParameter(**p) for p in params_raw],
        papers=[ParsedPaperRef(**p) for p in papers_raw],
        links=[ParsedLink(**l) for l in links_raw],
        asb_task_ids=_task_ids_from_provenance(provenance_raw, frontmatter),
        bundle_dir=str(skill_dir.relative_to(skill_dir.parent.parent)),
    )


def _split_frontmatter(skill_md: Path) -> tuple[dict, str]:
    if not skill_md.exists():
        return {}, ""
    text = skill_md.read_text()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        logger.warning("asb_skill_frontmatter_unparseable", path=str(skill_md))
        meta = {}
    body = parts[2].lstrip("\n")
    return meta, body


def _load_json(path: Path, *, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("asb_sidecar_unparseable", path=str(path))
        return default


def _task_ids_from_provenance(provenance: dict, frontmatter: dict) -> list[str]:
    ids: list[str] = []
    # frontmatter.provenance.source_task_ids
    fm_prov = (frontmatter or {}).get("provenance") or {}
    ids.extend(fm_prov.get("source_task_ids") or [])
    # artifact_provenance.json may also carry task ids
    for k in ("source_task_ids", "task_ids"):
        ids.extend((provenance or {}).get(k) or [])
    # dedup, preserve order
    seen, out = set(), []
    for t in ids:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_skill_parser.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/pipeline/asb/__init__.py src/perspicacite/pipeline/asb/models.py src/perspicacite/pipeline/asb/skill_parser.py tests/unit/test_asb_skill_parser.py
git commit -m "feat(asb): skill-bundle parser (skills/_index.json + per-skill sidecars)"
```

---

### Task 4: ASB workflow-card parser

**Files:**
- Modify: `src/perspicacite/pipeline/asb/models.py` (add `ParsedCard`)
- Create: `src/perspicacite/pipeline/asb/card_parser.py`
- Test: `tests/unit/test_asb_card_parser.py`

- [ ] **Step 1: Write the failing card-parser test**

```python
# tests/unit/test_asb_card_parser.py
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_parse_cards_finds_two():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    cards = parse_cards(FIXTURE)
    ids = {c.task_id for c in cards}
    assert ids == {"task_001", "task_002"}


def test_parse_card_extracts_skills_and_tools():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    cards = {c.task_id: c for c in parse_cards(FIXTURE)}
    task1 = cards["task_001"]
    assert "metabolite-identifier-mapping" in task1.skills_used
    assert "MetLinkR" in task1.tools_used


def test_parse_card_includes_body_text():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    cards = {c.task_id: c for c in parse_cards(FIXTURE)}
    assert cards["task_001"].body_markdown
    assert "metabolite" in cards["task_001"].body_markdown.lower()


def test_parse_card_extracts_domain_facets():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    cards = {c.task_id: c for c in parse_cards(FIXTURE)}
    t1 = cards["task_001"]
    assert t1.domain  # non-empty
    assert "metabolomics" in t1.domain.lower() or "metabolomics" in (t1.primary_domain or "").lower()


def test_parse_card_extracts_evaluation_strategy():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    cards = {c.task_id: c for c in parse_cards(FIXTURE)}
    t1 = cards["task_001"]
    # evaluation_strategy is a dict with direct_checks / expert_review keys
    assert isinstance(t1.evaluation_strategy, dict)
    # at least one of the two keys present
    assert any(k in t1.evaluation_strategy for k in ("direct_checks", "expert_review"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_card_parser.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Add `ParsedCard` to `models.py`**

```python
# append to src/perspicacite/pipeline/asb/models.py

class ParsedCard(BaseModel):
    """Result of parsing one cards/task_NNN.{md,json} pair."""
    model_config = ConfigDict(extra="allow")

    task_id: str                              # e.g. "task_001"
    title: str = ""                           # human-readable card title
    article_type: str | None = None
    domain: str | None = None
    primary_domain: str | None = None
    subdomains: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    subtask_categories: list[str] = Field(default_factory=list)

    crossref_doi: str | None = None
    github: str | None = None

    tools_used: list[str] = Field(default_factory=list)      # tool names
    skills_used: list[str] = Field(default_factory=list)     # skill slugs

    data_in: list[dict] = Field(default_factory=list)
    data_out: list[dict] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    landmark_outputs: list[str] = Field(default_factory=list)
    parameters: list[dict] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)
    evaluation_strategy: dict = Field(default_factory=dict)
    methodology_summary: list[str] = Field(default_factory=list)
    workflow_ports: dict = Field(default_factory=dict)

    body_markdown: str = ""
    schema_version: str | None = None
```

- [ ] **Step 4: Write the parser**

```python
# src/perspicacite/pipeline/asb/card_parser.py
"""Parser for ASB workflow cards (cards/task_NNN.{md,json}).

A card is a richly-structured scientific task. The .json file is
the source of truth for structured fields; the .md file carries
the human-readable body that gets chunked + embedded.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from perspicacite.pipeline.asb.models import ParsedCard

logger = logging.getLogger(__name__)


def parse_cards(run_dir: Path | str) -> list[ParsedCard]:
    """Return one ParsedCard per task_NNN.json under cards/.

    A card is included only when both .json and .md exist (matched
    by task_id). Pairs missing one half are skipped with a warning.
    """
    run_dir = Path(run_dir)
    cards_dir = run_dir / "cards"
    if not cards_dir.is_dir():
        return []

    json_paths = sorted(cards_dir.glob("task_*.json"))
    out: list[ParsedCard] = []
    for jp in json_paths:
        task_id = jp.stem  # "task_001"
        mp = cards_dir / f"{task_id}.md"
        if not mp.exists():
            logger.warning("asb_card_missing_md", task_id=task_id)
            continue
        try:
            structured = json.loads(jp.read_text())
        except json.JSONDecodeError:
            logger.warning("asb_card_json_unparseable", path=str(jp))
            continue
        body = mp.read_text()
        out.append(_card_from_json(task_id=task_id, structured=structured, body=body))
    return out


def _card_from_json(*, task_id: str, structured: dict, body: str) -> ParsedCard:
    # The .json schema names vary slightly across ASB versions; tolerate.
    return ParsedCard(
        task_id=task_id,
        title=structured.get("title")
              or structured.get("research_question")
              or task_id,
        article_type=structured.get("article_type"),
        domain=structured.get("domain"),
        primary_domain=structured.get("primary_domain"),
        subdomains=structured.get("subdomains") or [],
        techniques=structured.get("techniques") or [],
        subtask_categories=structured.get("subtask_categories") or [],
        crossref_doi=structured.get("crossref_doi") or structured.get("doi"),
        github=structured.get("github"),
        tools_used=structured.get("tools") or [],
        skills_used=structured.get("skills") or [],
        data_in=structured.get("data_in") or [],
        data_out=structured.get("data_out") or [],
        expected_outputs=structured.get("expected_outputs") or [],
        landmark_outputs=structured.get("landmark_outputs") or [],
        parameters=structured.get("parameters") or [],
        domain_knowledge=structured.get("domain_knowledge") or [],
        evaluation_strategy=structured.get("evaluation_strategy") or {},
        methodology_summary=structured.get("methodology_summary") or [],
        workflow_ports=structured.get("workflow_ports") or {},
        body_markdown=body,
        schema_version=structured.get("schema_version"),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_card_parser.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/pipeline/asb/models.py src/perspicacite/pipeline/asb/card_parser.py tests/unit/test_asb_card_parser.py
git commit -m "feat(asb): workflow-card parser (cards/task_NNN.{md,json})"
```

---

### Task 5: `workflow_dag.json` reader + upstream/downstream maps

**Files:**
- Create: `src/perspicacite/pipeline/asb/dag.py`
- Test: `tests/unit/test_asb_dag.py`

- [ ] **Step 1: Write the failing DAG test**

```python
# tests/unit/test_asb_dag.py
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_load_dag_returns_nodes_and_edges():
    from perspicacite.pipeline.asb.dag import load_workflow_dag
    dag = load_workflow_dag(FIXTURE)
    assert "task_001" in dag.nodes
    assert ("task_001", "task_002") in dag.edges


def test_dag_upstream_downstream_maps():
    from perspicacite.pipeline.asb.dag import load_workflow_dag
    dag = load_workflow_dag(FIXTURE)
    # task_001 → task_002 → task_003 → ...
    assert "task_002" in dag.downstream("task_001")
    assert "task_001" in dag.upstream("task_002")
    # Isolated nodes return empty
    assert dag.upstream("task_001") == []


def test_dag_missing_file_returns_empty():
    from perspicacite.pipeline.asb.dag import load_workflow_dag
    dag = load_workflow_dag(Path("/tmp/__no_such_asb_run__"))
    assert dag.nodes == []
    assert dag.edges == []
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_dag.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/perspicacite/pipeline/asb/dag.py
"""Workflow DAG reader (workflow_dag.json).

The DAG is bundle-level metadata, stored on the KB description
and surfaced in auto-KB-routing responses. v1 does not index
edges as chunks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowDag:
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def upstream(self, task_id: str) -> list[str]:
        return [src for (src, dst) in self.edges if dst == task_id]

    def downstream(self, task_id: str) -> list[str]:
        return [dst for (src, dst) in self.edges if src == task_id]

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes),
            "edges": [list(e) for e in self.edges],
        }


def load_workflow_dag(run_dir: Path | str) -> WorkflowDag:
    """Return the workflow DAG. Missing or invalid file → empty DAG."""
    p = Path(run_dir) / "workflow_dag.json"
    if not p.exists():
        return WorkflowDag()
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError:
        return WorkflowDag()
    nodes = list(raw.get("nodes", []))
    edges_raw = raw.get("edges", [])
    edges = [
        (e[0], e[1]) for e in edges_raw
        if isinstance(e, (list, tuple)) and len(e) == 2
    ]
    return WorkflowDag(nodes=nodes, edges=edges)
```

- [ ] **Step 4: Run**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_dag.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/asb/dag.py tests/unit/test_asb_dag.py
git commit -m "feat(asb): workflow_dag.json reader with upstream/downstream maps"
```

---

### Task 6: Chunk producer (ParsedSkill | ParsedCard → list[Paper])

**Files:**
- Create: `src/perspicacite/pipeline/asb/chunk_producer.py`
- Test: `tests/unit/test_asb_chunk_producer.py`

The chunk producer converts parsed records into Papers carrying ASB metadata in `Paper.metadata`. Paper IDs are stable strings derived from `(content_kind, slug-or-task-id)` so re-ingest is idempotent. Each Paper gets `source=PaperSource.SKILL_BUNDLE`. The existing chunker reads `Paper.full_text` and propagates `Paper.metadata` to chunks.

- [ ] **Step 1: Write the failing chunk-producer test**

```python
# tests/unit/test_asb_chunk_producer.py
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_skill_becomes_paper_with_correct_metadata():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    from perspicacite.pipeline.asb.chunk_producer import skill_to_paper
    from perspicacite.models.papers import PaperSource

    skill = parse_skill_bundle(FIXTURE)[0]
    paper = skill_to_paper(skill)
    assert paper.source is PaperSource.SKILL_BUNDLE
    assert paper.id == "asb_skill:cross-identifier-reconciliation"
    assert paper.full_text  # the skill.md body
    md = paper.metadata
    assert md["content_kind"] == "skill_body"
    assert md["skill_id"] == "cross-identifier-reconciliation"
    assert md["skill_name"] == "cross-identifier-reconciliation"
    assert isinstance(md["tools"], list)
    assert isinstance(md["environment"], list)
    assert isinstance(md["parameters"], list)


def test_card_becomes_paper_with_workflow_metadata():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    from perspicacite.pipeline.asb.chunk_producer import card_to_paper
    from perspicacite.models.papers import PaperSource

    card = next(c for c in parse_cards(FIXTURE) if c.task_id == "task_001")
    paper = card_to_paper(card, dag=None)
    assert paper.source is PaperSource.SKILL_BUNDLE
    assert paper.id == "asb_card:task_001"
    md = paper.metadata
    assert md["content_kind"] == "workflow_card"
    assert md["task_id"] == "task_001"
    assert "MetLinkR" in md["tools_used"]
    assert md["domain"]


def test_card_to_paper_attaches_dag_neighbors():
    from perspicacite.pipeline.asb.card_parser import parse_cards
    from perspicacite.pipeline.asb.chunk_producer import card_to_paper
    from perspicacite.pipeline.asb.dag import load_workflow_dag

    dag = load_workflow_dag(FIXTURE)
    card = next(c for c in parse_cards(FIXTURE) if c.task_id == "task_001")
    paper = card_to_paper(card, dag=dag)
    md = paper.metadata
    assert md["downstream_tasks"] == ["task_002"]
    assert md["upstream_tasks"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_chunk_producer.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/perspicacite/pipeline/asb/chunk_producer.py
"""Convert parsed ASB records → Paper objects.

Each ASB skill / workflow card maps to one Paper with
``source=PaperSource.SKILL_BUNDLE`` and the structured fields in
``Paper.metadata``. The existing chunker reads Paper.full_text
and propagates Paper.metadata onto chunk metadata.

Paper IDs are stable (asb_skill:{slug} / asb_card:{task_id}) so
re-ingest is idempotent against ``DynamicKnowledgeBase._paper_ids``.
"""
from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.asb.dag import WorkflowDag
from perspicacite.pipeline.asb.models import ParsedCard, ParsedSkill


def skill_to_paper(skill: ParsedSkill) -> Paper:
    """Return a Paper carrying the skill body + per-chunk metadata."""
    md = {
        "content_kind": "skill_body",
        "skill_id": skill.slug,
        "skill_name": skill.name,
        "skill_description": skill.description,
        "edam_operation": skill.edam_operation,
        "edam_topics": list(skill.edam_topics),
        "tools": [t.model_dump() for t in skill.tools],
        "environment": [e.model_dump() for e in skill.environments],
        "parameters": [p.model_dump() for p in skill.parameters],
        "when_to_use_negative": list(skill.when_to_use_negative),
        "asb_task_ids": list(skill.asb_task_ids),
        "schema_version": skill.schema_version,
    }
    return Paper(
        id=f"asb_skill:{skill.slug}",
        title=skill.name,
        abstract=skill.description,
        full_text=skill.body_markdown,
        source=PaperSource.SKILL_BUNDLE,
        metadata=md,
    )


def card_to_paper(card: ParsedCard, *, dag: WorkflowDag | None) -> Paper:
    """Return a Paper carrying the card body + per-chunk metadata."""
    md = {
        "content_kind": "workflow_card",
        "task_id": card.task_id,
        "task_card_title": card.title,
        "article_type": card.article_type,
        "domain": card.domain,
        "primary_domain": card.primary_domain,
        "subdomains": list(card.subdomains),
        "techniques": list(card.techniques),
        "subtask_categories": list(card.subtask_categories),
        "tools_used": list(card.tools_used),
        "skills_used": list(card.skills_used),
        "paper_doi": card.crossref_doi,
        "paper_github": card.github,
        "inputs": list(card.data_in),
        "expected_outputs": list(card.expected_outputs),
        "parameters": list(card.parameters),
        "evaluation_strategy": dict(card.evaluation_strategy),
        "schema_version": card.schema_version,
        "upstream_tasks": dag.upstream(card.task_id) if dag else [],
        "downstream_tasks": dag.downstream(card.task_id) if dag else [],
    }
    return Paper(
        id=f"asb_card:{card.task_id}",
        title=card.title,
        abstract="",
        full_text=card.body_markdown,
        source=PaperSource.SKILL_BUNDLE,
        doi=card.crossref_doi,
        metadata=md,
    )
```

- [ ] **Step 4: Run**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_chunk_producer.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/asb/chunk_producer.py tests/unit/test_asb_chunk_producer.py
git commit -m "feat(asb): chunk producer — Paper builder for skills + cards + DAG"
```

---

### Task 7: `skill_kb.json` writer (in-place round-trip)

**Files:**
- Create: `src/perspicacite/pipeline/asb/skill_kb_writer.py`
- Test: `tests/unit/test_asb_skill_kb_writer.py`

- [ ] **Step 1: Write the failing round-trip test**

```python
# tests/unit/test_asb_skill_kb_writer.py
import json
import shutil
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_write_entries_updates_skill_kb_json(tmp_path):
    from perspicacite.pipeline.asb.skill_kb_writer import write_skill_kb_entries

    # Copy the fixture to tmp_path so we don't mutate the checked-in file
    target = tmp_path / "run"
    shutil.copytree(FIXTURE, target)
    skill_kb = (target / "skills" / "cross-identifier-reconciliation"
                / "skill_kb.json")
    before = json.loads(skill_kb.read_text())
    assert before["entries"] == []

    entries = [
        {
            "kind": "skill_body",
            "source_url": "skills/cross-identifier-reconciliation/skill.md",
            "kb_name": "metlinkr_bundle",
            "chunk_ids": ["c1", "c2"],
            "chunk_count": 2,
            "bytes": 5400,
            "content_type": "text",
            "embedding_model": "text-embedding-3-small",
            "ingested_at": "2026-05-15T20:30:00Z",
        }
    ]
    n = write_skill_kb_entries(skill_kb, entries=entries)
    assert n == 1

    after = json.loads(skill_kb.read_text())
    assert len(after["entries"]) == 1
    assert after["total_bytes"] == 5400
    assert "perspicacite_ingest_completed" in after.get("notes", "")
    # Original ASB notes preserved
    assert "no repo URLs" in after.get("notes", "")


def test_write_entries_idempotent_by_source_url(tmp_path):
    from perspicacite.pipeline.asb.skill_kb_writer import write_skill_kb_entries

    target = tmp_path / "run"
    shutil.copytree(FIXTURE, target)
    skill_kb = (target / "skills" / "cross-identifier-reconciliation"
                / "skill_kb.json")

    entries = [{"kind": "skill_body", "source_url": "skills/.../skill.md",
                "kb_name": "kb", "chunk_ids": [], "chunk_count": 0,
                "bytes": 100, "content_type": "text",
                "embedding_model": "x", "ingested_at": "2026-05-15T20:00Z"}]
    write_skill_kb_entries(skill_kb, entries=entries)
    write_skill_kb_entries(skill_kb, entries=entries)  # re-run
    after = json.loads(skill_kb.read_text())
    # idempotent: same source_url is replaced, not duplicated
    assert len(after["entries"]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_skill_kb_writer.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/perspicacite/pipeline/asb/skill_kb_writer.py
"""In-place writer for skills/{slug}/skill_kb.json.

Preserves ASB's original notes; appends Perspicacité's completion
stamp. Idempotent against re-ingest: entries keyed by source_url.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_skill_kb_entries(
    skill_kb_path: Path | str,
    *,
    entries: list[dict],
) -> int:
    """Update the skill_kb.json file at ``skill_kb_path``.

    Entries with the same ``source_url`` as an existing entry are
    replaced (not duplicated). Returns the total number of entries
    after the update.
    """
    path = Path(skill_kb_path)
    if not path.exists():
        raise FileNotFoundError(f"skill_kb.json not found at {path}")
    data = json.loads(path.read_text())
    existing: list[dict] = data.get("entries") or []
    by_url = {e.get("source_url"): e for e in existing}
    for e in entries:
        by_url[e.get("source_url")] = e
    merged = list(by_url.values())

    data["entries"] = merged
    data["total_bytes"] = sum(int(e.get("bytes") or 0) for e in merged)
    data["truncated"] = any(bool(e.get("truncated")) for e in merged)

    stamp = f"perspicacite_ingest_completed={_now_iso()}"
    original_notes = data.get("notes") or ""
    if "perspicacite_ingest_completed=" in original_notes:
        # Replace the previous stamp inline
        prefix, _sep, _rest = original_notes.partition("perspicacite_ingest_completed=")
        # _rest may contain whitespace + previous stamp; drop everything past the prefix
        data["notes"] = (prefix + stamp).strip()
    else:
        sep = " | " if original_notes else ""
        data["notes"] = f"{original_notes}{sep}{stamp}"

    path.write_text(json.dumps(data, indent=2) + "\n")
    return len(merged)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Run**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_skill_kb_writer.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/asb/skill_kb_writer.py tests/unit/test_asb_skill_kb_writer.py
git commit -m "feat(asb): skill_kb.json round-trip writer (in-place, idempotent)"
```

---

### Task 8: Top-level orchestrator

**Files:**
- Create: `src/perspicacite/pipeline/asb/run_ingest.py`
- Test: `tests/integration/test_asb_run_ingest_end_to_end.py`

The orchestrator wires parsers → chunk producer → `DynamicKnowledgeBase.add_papers`. Backing-paper DOIs from `papers.json` go through the existing `ingest_dois_into_kb` path. The DAG is stored as KB-level metadata. `skill_kb.json` is updated per-skill after a successful pass.

This task is integration-shaped (touches storage + chunker + embedder). Mock the embedder + the DOI ingest in unit-test mode; assert structure rather than vector counts.

- [ ] **Step 1: Write the orchestrator integration test (skeleton, mocked)**

```python
# tests/integration/test_asb_run_ingest_end_to_end.py
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


@pytest.mark.asyncio
async def test_ingest_asb_run_composite_mode(tmp_path):
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(FIXTURE, target)

    # Mock the KB layer; track what add_papers receives
    added_papers: list = []

    fake_kb = MagicMock()
    fake_kb.name = "metlinkr_bundle"
    fake_kb.description = ""
    fake_kb.add_papers = AsyncMock(
        side_effect=lambda papers, **kw: added_papers.extend(papers) or len(papers)
    )

    async def fake_make_or_get_kb(name: str, description: str = "", **kw):
        fake_kb.name = name
        fake_kb.description = description
        return fake_kb

    async def fake_ingest_dois(*, dois, kb, **kw):
        return {"added": len(dois), "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest.ingest_dois_into_kb",
        side_effect=fake_ingest_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="metlinkr_bundle",
            include=("skills", "workflows"),
            mode="composite",
        )

    assert result["kb_names"] == ["metlinkr_bundle"]
    assert result["skills_ingested"] == 1
    assert result["workflows_ingested"] == 2
    assert result["papers_ingested"] >= 1
    # Each parsed record became a Paper
    paper_ids = {p.id for p in added_papers}
    assert "asb_skill:cross-identifier-reconciliation" in paper_ids
    assert "asb_card:task_001" in paper_ids
    assert "asb_card:task_002" in paper_ids
    # DAG stored on KB description (JSON-encoded under "workflow_dag" key)
    assert "workflow_dag" in (fake_kb.description or "")
    # skill_kb.json updated
    sk = json.loads((target / "skills" / "cross-identifier-reconciliation"
                     / "skill_kb.json").read_text())
    assert sk["entries"]


@pytest.mark.asyncio
async def test_ingest_asb_run_include_skills_only(tmp_path):
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(FIXTURE, target)

    added_papers: list = []
    fake_kb = MagicMock()
    fake_kb.name = "kb"
    fake_kb.description = ""
    fake_kb.add_papers = AsyncMock(
        side_effect=lambda papers, **kw: added_papers.extend(papers) or len(papers)
    )

    async def fake_make_or_get_kb(name, description="", **kw):
        fake_kb.name = name
        return fake_kb

    async def fake_ingest_dois(*, dois, kb, **kw):
        return {"added": 0, "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest.ingest_dois_into_kb",
        side_effect=fake_ingest_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="kb",
            include=("skills",),
            mode="composite",
        )
    assert result["workflows_ingested"] == 0
    paper_ids = {p.id for p in added_papers}
    assert not any(p.startswith("asb_card:") for p in paper_ids)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/integration/test_asb_run_ingest_end_to_end.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement the orchestrator**

```python
# src/perspicacite/pipeline/asb/run_ingest.py
"""Top-level ASB-run ingestion.

Steps:
  1. Parse skills (skill_parser.parse_skill_bundle)
  2. Parse workflow cards (card_parser.parse_cards)
  3. Load workflow DAG (dag.load_workflow_dag)
  4. Get/create the KB (one per --per-skill or one composite)
  5. For each ParsedSkill: build a Paper + add to KB; ingest
     backing-paper DOIs via existing ingest_dois_into_kb
  6. For each ParsedCard: build a Paper (with DAG neighbors) + add
  7. Store workflow_dag.json contents on the KB description
  8. Write skill_kb.json entries per skill
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from perspicacite.models.papers import Paper
from perspicacite.pipeline.asb.card_parser import parse_cards
from perspicacite.pipeline.asb.chunk_producer import card_to_paper, skill_to_paper
from perspicacite.pipeline.asb.dag import WorkflowDag, load_workflow_dag
from perspicacite.pipeline.asb.models import ParsedSkill
from perspicacite.pipeline.asb.skill_kb_writer import write_skill_kb_entries
from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb

logger = logging.getLogger(__name__)


async def ingest_asb_run(
    *,
    asb_run_dir: str | Path,
    kb_name: str | None = None,
    include: Iterable[str] = ("skills", "workflows"),
    mode: str = "composite",
    update_skill_kb_json: bool = True,
) -> dict[str, Any]:
    """Ingest an ASB run directory into one or more Perspicacité KBs.

    Args:
        asb_run_dir: Path to the run dir (must contain skills/_index.json
            and/or cards/).
        kb_name: KB to write to. Defaults to the run-dir name.
        include: Which artifact streams to ingest. Subset of
            {"skills", "workflows"}.
        mode: "composite" (single KB) or "per-skill" (one KB per skill).
            Workflows always land in the composite KB regardless of mode.
        update_skill_kb_json: Write back the integration seam.

    Returns:
        ``{"kb_names": [...], "skills_ingested": int,
          "workflows_ingested": int, "papers_ingested": int,
          "repos_fetched": int, "failed": [...], "total_chunks": int,
          "workflow_dag": {nodes, edges} | None}``
    """
    run_dir = Path(asb_run_dir)
    include = set(include)
    if not include:
        raise ValueError("include must contain at least one of {skills, workflows}")
    if mode not in ("composite", "per-skill"):
        raise ValueError("mode must be 'composite' or 'per-skill'")

    skills = parse_skill_bundle(run_dir) if "skills" in include else []
    cards = parse_cards(run_dir) if "workflows" in include else []
    dag = load_workflow_dag(run_dir)

    composite_name = kb_name or run_dir.name
    kb_names: list[str] = []
    papers_ingested = 0
    total_chunks = 0
    failed: list[dict] = []

    # 1. Composite KB (always created; holds workflows + papers + skills
    # when mode=composite). For mode=per-skill, the composite KB still
    # holds workflows + papers.
    composite_kb = await _make_or_get_kb(
        composite_name,
        description=_kb_description(skills=skills, cards=cards, dag=dag),
    )
    kb_names.append(composite_name)

    # 2. Skills → composite or per-skill KBs
    for skill in skills:
        target_kb = composite_kb
        if mode == "per-skill":
            target_kb = await _make_or_get_kb(
                f"{composite_name}__{skill.slug}",
                description=_kb_description(skills=[skill], cards=[], dag=dag),
            )
            kb_names.append(f"{composite_name}__{skill.slug}")

        # 2a. Skill body
        try:
            paper = skill_to_paper(skill)
            n = await target_kb.add_papers([paper])
            total_chunks += n
        except Exception as e:
            failed.append({"slug": skill.slug, "stage": "skill_body", "error": str(e)})
            continue

        # 2b. Backing-paper DOIs (real papers, normal pipeline)
        dois = [p.doi for p in skill.papers if p.doi]
        if dois:
            try:
                res = await ingest_dois_into_kb(dois=dois, kb=target_kb)
                papers_ingested += int(res.get("added", 0))
            except Exception as e:
                failed.append({"slug": skill.slug, "stage": "papers", "error": str(e)})

        # 2c. skill_kb.json round-trip
        if update_skill_kb_json:
            skill_kb_path = run_dir / "skills" / skill.slug / "skill_kb.json"
            if skill_kb_path.exists():
                try:
                    write_skill_kb_entries(
                        skill_kb_path,
                        entries=_skill_kb_entries(skill=skill, kb_name=target_kb.name),
                    )
                except Exception as e:
                    failed.append(
                        {"slug": skill.slug, "stage": "skill_kb_json", "error": str(e)}
                    )

    # 3. Workflows → composite KB only
    for card in cards:
        try:
            paper = card_to_paper(card, dag=dag)
            n = await composite_kb.add_papers([paper])
            total_chunks += n
        except Exception as e:
            failed.append({"task_id": card.task_id, "stage": "card", "error": str(e)})

    return {
        "kb_names": kb_names,
        "skills_ingested": len(skills),
        "workflows_ingested": len(cards),
        "papers_ingested": papers_ingested,
        "repos_fetched": 0,  # v1 defers repo fetching
        "total_chunks": total_chunks,
        "failed": failed,
        "workflow_dag": dag.to_dict() if dag.nodes else None,
    }


async def _make_or_get_kb(name: str, *, description: str = "", **kw):
    """Indirection so tests can patch this. Real impl delegates to the
    KB registry / factory used by the rest of the codebase."""
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase

    kb = DynamicKnowledgeBase(name=name, description=description, **kw)
    await kb.initialize()
    return kb


def _kb_description(
    *,
    skills: list[ParsedSkill],
    cards: list,
    dag: WorkflowDag,
) -> str:
    """Build a KB description JSON-blob that includes workflow_dag
    + a skill catalog. Stored as the description string so it travels
    with the KB metadata."""
    payload = {
        "asb_bundle": True,
        "skills": [{"slug": s.slug, "description": s.description[:200]} for s in skills],
        "card_count": len(cards),
        "workflow_dag": dag.to_dict() if dag.nodes else None,
    }
    return json.dumps(payload)


def _skill_kb_entries(*, skill: ParsedSkill, kb_name: str) -> list[dict]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bytes_body = len(skill.body_markdown.encode("utf-8"))
    return [
        {
            "kind": "skill_body",
            "source_url": f"skills/{skill.slug}/skill.md",
            "kb_name": kb_name,
            "chunk_ids": [],   # populated later when chunker exposes IDs
            "chunk_count": 0,
            "bytes": bytes_body,
            "content_type": "text",
            "embedding_model": "text-embedding-3-small",
            "ingested_at": ts,
        }
    ]
```

- [ ] **Step 4: Run the integration test**

Run: `PYTHONPATH=src pytest tests/integration/test_asb_run_ingest_end_to_end.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/asb/run_ingest.py tests/integration/test_asb_run_ingest_end_to_end.py
git commit -m "feat(asb): top-level run-ingest orchestrator (skills + workflows + DAG)"
```

---

### Task 9: MCP tool `ingest_asb_run`

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (add a tool near the existing skill-ingest tools)
- Test: `tests/unit/test_mcp_ingest_asb_run.py`

- [ ] **Step 1: Write the failing MCP-wiring test**

```python
# tests/unit/test_mcp_ingest_asb_run.py
import inspect


def test_mcp_server_exports_ingest_asb_run():
    """Verify the MCP server defines an ingest_asb_run tool."""
    from perspicacite.mcp import server

    src = inspect.getsource(server)
    assert "async def ingest_asb_run" in src
    assert "@mcp.tool()" in src  # decorator is present (any tool — at least one)
    # The new tool takes the expected arg names
    assert "asb_run_dir" in src
    assert "include" in src
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_ingest_asb_run.py -v`
Expected: FAIL — the source string `async def ingest_asb_run` doesn't appear yet.

- [ ] **Step 3: Add the MCP tool**

Insert near the existing `add_papers_to_kb` / DOI-ingest tools in `src/perspicacite/mcp/server.py`:

```python
@mcp.tool()
async def ingest_asb_run(
    asb_run_dir: str,
    kb_name: str | None = None,
    include: list[str] | None = None,
    mode: str = "composite",
    update_skill_kb_json: bool = True,
) -> dict:
    """Ingest an Agent Skill Bundle (ASB) run directory into a KB.

    Args:
        asb_run_dir: Filesystem path to an ASB run directory.
        kb_name: Target KB name (default: derive from run-dir name).
        include: Which artifact streams to ingest. Subset of
            ["skills", "workflows"]. Default: both.
        mode: "composite" (one KB) or "per-skill" (one KB per skill;
            workflows still land in the composite KB).
        update_skill_kb_json: Write the integration seam back to
            each skill_kb.json after ingest.

    Returns: orchestrator result dict — kb_names, skills_ingested,
        workflows_ingested, papers_ingested, total_chunks, failed,
        workflow_dag.
    """
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run as _run

    return await _run(
        asb_run_dir=asb_run_dir,
        kb_name=kb_name,
        include=tuple(include) if include else ("skills", "workflows"),
        mode=mode,
        update_skill_kb_json=update_skill_kb_json,
    )
```

- [ ] **Step 4: Run**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_ingest_asb_run.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_ingest_asb_run.py
git commit -m "feat(mcp): ingest_asb_run tool for ASB-run KB ingest"
```

---

### Task 10: CLI command `ingest-asb-run`

**Files:**
- Modify: `src/perspicacite/cli.py` (Click-based; existing commands at lines ~61, 143, 270, 334, 387, 433, 649, 740, 813 — add the new command adjacent to `add-to-kb`/`ingest-local` which are the closest cousins)
- Test: `tests/unit/test_cli_ingest_asb_run.py`

- [ ] **Step 1: Write the failing CLI test**

```python
# tests/unit/test_cli_ingest_asb_run.py
from unittest.mock import AsyncMock, patch


def test_cli_ingest_asb_run_help():
    """The CLI exposes an ingest-asb-run command with the right flags."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest-asb-run", "--help"])
    assert result.exit_code == 0
    assert "ASB_RUN_DIR" in result.output or "asb-run-dir" in result.output.lower()
    assert "include" in result.output
    assert "mode" in result.output


def test_cli_ingest_asb_run_dispatches_to_orchestrator(tmp_path):
    from click.testing import CliRunner
    from perspicacite.cli import cli

    fake = AsyncMock(return_value={
        "kb_names": ["kb"], "skills_ingested": 0,
        "workflows_ingested": 0, "papers_ingested": 0,
        "total_chunks": 0, "failed": [], "workflow_dag": None,
        "repos_fetched": 0,
    })
    with patch("perspicacite.pipeline.asb.run_ingest.ingest_asb_run", fake):
        runner = CliRunner()
        result = runner.invoke(cli, [
            "ingest-asb-run", str(tmp_path), "--kb-name", "kb",
            "--include", "skills",
        ])
    assert result.exit_code == 0
    fake.assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_cli_ingest_asb_run.py -v`
Expected: FAIL — `No such command 'ingest-asb-run'`.

- [ ] **Step 3: Add the CLI command**

Insert in `src/perspicacite/cli.py` near the other `@cli.command()` definitions (it's a `click.group()` named `cli`, established at the top of the file):

```python
@cli.command(name="ingest-asb-run")
@click.argument("asb_run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--kb-name", default=None, help="Target KB name (default: run-dir name)")
@click.option(
    "--include",
    default="skills,workflows",
    help="Comma-separated artifact streams to ingest "
         "(subset of skills,workflows; default: both)",
)
@click.option(
    "--mode",
    type=click.Choice(["composite", "per-skill"]),
    default="composite",
    help="Composite (one KB) or per-skill (one KB per skill; "
         "workflows still land in the composite KB).",
)
@click.option(
    "--update-skill-kb-json/--no-update-skill-kb-json",
    default=True,
    help="Write the integration seam back to each skill_kb.json "
         "after ingest (default: yes).",
)
def ingest_asb_run_cmd(asb_run_dir, kb_name, include, mode, update_skill_kb_json):
    """Ingest an Agent Skill Bundle (ASB) run directory into a KB."""
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    parts = tuple(p.strip() for p in include.split(",") if p.strip())
    result = asyncio.run(ingest_asb_run(
        asb_run_dir=asb_run_dir,
        kb_name=kb_name,
        include=parts,
        mode=mode,
        update_skill_kb_json=update_skill_kb_json,
    ))
    click.echo(f"Skills ingested:    {result['skills_ingested']}")
    click.echo(f"Workflows ingested: {result['workflows_ingested']}")
    click.echo(f"Papers ingested:    {result['papers_ingested']}")
    click.echo(f"Total chunks:       {result['total_chunks']}")
    click.echo(f"KBs:                {', '.join(result['kb_names'])}")
    if result["failed"]:
        click.echo(f"Failures:           {len(result['failed'])}")
        for f in result["failed"]:
            click.echo(f"  - {f}")
```

- [ ] **Step 4: Run**

Run: `PYTHONPATH=src pytest tests/unit/test_cli_ingest_asb_run.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/cli.py tests/unit/test_cli_ingest_asb_run.py
git commit -m "feat(cli): ingest-asb-run command"
```

---

### Task 11: Auto-KB-routing response: `skill_metadata` + `workflow_metadata`

**Files:**
- Modify: `src/perspicacite/web/routers/chat.py` (response builder around `auto_route_kbs` usage at ~line 484)
- Modify: `src/perspicacite/mcp/server.py` (response builder around `auto_route_kbs` usage at ~line 1816)
- Test: `tests/unit/test_asb_response_metadata.py`

The response is built around `auto_route_kbs`'s `hits: list[KBRouteHit]`. After fetching top-K chunks, the response builder must group chunks by `metadata["content_kind"]`:

- chunks where `content_kind` starts with `skill_` → produce one entry per distinct `skill_id` in `skill_metadata`
- chunks where `content_kind == "workflow_card"` → produce one entry per distinct `task_id` in `workflow_metadata`

A helper function (`build_asb_response_metadata`) lives in a shared module so both chat.py and mcp/server.py can call it.

- [ ] **Step 1: Write the failing test for the helper**

```python
# tests/unit/test_asb_response_metadata.py
def test_build_asb_response_metadata_groups_skills():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {"content_kind": "skill_body", "skill_id": "abc",
                      "skill_name": "Abc", "tools": [{"name": "T1", "canonical_url": "u1"}],
                      "environment": [{"language": "R"}], "parameters": []}},
        # Duplicate skill_id → coalesced
        {"metadata": {"content_kind": "skill_body", "skill_id": "abc",
                      "skill_name": "Abc", "tools": [], "environment": [], "parameters": []}},
        {"metadata": {"content_kind": "skill_body", "skill_id": "xyz",
                      "skill_name": "Xyz", "tools": [], "environment": [], "parameters": []}},
    ]
    out = build_asb_response_metadata(chunks)
    assert {s["skill_id"] for s in out["skill_metadata"]} == {"abc", "xyz"}
    assert out["workflow_metadata"] == []


def test_build_asb_response_metadata_groups_workflows():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {"content_kind": "workflow_card", "task_id": "task_001",
                      "task_card_title": "T1", "domain": "metabolomics",
                      "skills_used": ["s1"], "tools_used": ["T"],
                      "parameters": [], "expected_outputs": [],
                      "evaluation_strategy": {}, "paper_doi": "10.x/y",
                      "paper_github": "org/repo", "downstream_tasks": ["task_002"],
                      "upstream_tasks": []}},
    ]
    out = build_asb_response_metadata(chunks)
    assert out["skill_metadata"] == []
    assert len(out["workflow_metadata"]) == 1
    wm = out["workflow_metadata"][0]
    assert wm["task_id"] == "task_001"
    assert wm["downstream_tasks"] == ["task_002"]


def test_build_asb_response_metadata_mixed_and_unrelated_chunks():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {}},                                              # ignored
        {"metadata": {"content_kind": "skill_body", "skill_id": "s"}}, # skill
        {"metadata": {"content_kind": "workflow_card", "task_id": "t"}},
    ]
    out = build_asb_response_metadata(chunks)
    assert len(out["skill_metadata"]) == 1
    assert len(out["workflow_metadata"]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_response_metadata.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement the helper**

```python
# src/perspicacite/pipeline/asb/response.py
"""Response-time helper: derive skill_metadata + workflow_metadata
from a list of chunk dicts returned by the retrieval layer."""
from __future__ import annotations

from typing import Any


def build_asb_response_metadata(chunks: list[dict[str, Any]]) -> dict[str, list]:
    """Group ASB-sourced chunks into skill / workflow summary blocks.

    Args:
        chunks: list of chunk dicts. Each must have a ``metadata``
            mapping; chunks without ``content_kind`` are ignored.

    Returns:
        ``{"skill_metadata": [...], "workflow_metadata": [...]}``.
        Each list deduplicates by skill_id / task_id (first wins).
    """
    skill_map: dict[str, dict] = {}
    workflow_map: dict[str, dict] = {}

    for chunk in chunks:
        md = chunk.get("metadata") or {}
        kind = md.get("content_kind")
        if not kind:
            continue
        if kind.startswith("skill_"):
            skill_id = md.get("skill_id")
            if skill_id and skill_id not in skill_map:
                tools = md.get("tools") or []
                env = md.get("environment") or []
                params = md.get("parameters") or []
                executable = all(
                    bool(t.get("canonical_url")) and bool(t.get("install"))
                    for t in tools
                ) if tools else False
                skill_map[skill_id] = {
                    "skill_id": skill_id,
                    "skill_name": md.get("skill_name"),
                    "tool_requirements": [
                        {
                            "name": t.get("name"),
                            "canonical_url": t.get("canonical_url"),
                            "install": t.get("install"),
                        }
                        for t in tools
                    ],
                    "environment": env,
                    "parameters": params,
                    "executable": executable,
                    "asb_mcp_hint": f"asb://skill/{skill_id}",
                }
        elif kind == "workflow_card":
            task_id = md.get("task_id")
            if task_id and task_id not in workflow_map:
                workflow_map[task_id] = {
                    "task_id": task_id,
                    "title": md.get("task_card_title"),
                    "domain": md.get("domain"),
                    "skills_used": list(md.get("skills_used") or []),
                    "tools_used": list(md.get("tools_used") or []),
                    "parameters": list(md.get("parameters") or []),
                    "expected_outputs": list(md.get("expected_outputs") or []),
                    "evaluation_strategy": dict(md.get("evaluation_strategy") or {}),
                    "paper_doi": md.get("paper_doi"),
                    "paper_github": md.get("paper_github"),
                    "downstream_tasks": list(md.get("downstream_tasks") or []),
                    "upstream_tasks": list(md.get("upstream_tasks") or []),
                }

    return {
        "skill_metadata": list(skill_map.values()),
        "workflow_metadata": list(workflow_map.values()),
    }
```

- [ ] **Step 4: Wire the helper into both response builders**

In `src/perspicacite/web/routers/chat.py` (around the existing `auto_route_kbs` usage near line 484) and `src/perspicacite/mcp/server.py` (around line 1816), after the chunk-retrieval step, call:

```python
from perspicacite.pipeline.asb.response import build_asb_response_metadata

# chunks_dicts: the list of chunk records that already feed `sources`
asb_meta = build_asb_response_metadata(chunks_dicts)
response.update(asb_meta)  # adds "skill_metadata" / "workflow_metadata" keys
```

The implementer locates the exact response-dict assembly and inserts the call. Confirm both call sites carry the new keys by extending the existing end-to-end test (`tests/integration/test_asb_run_ingest_end_to_end.py`) with a check that a query → response payload contains the new keys when ASB chunks are in the hits — see Task 12.

- [ ] **Step 5: Run all tests**

Run: `PYTHONPATH=src pytest tests/unit/test_asb_response_metadata.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/pipeline/asb/response.py src/perspicacite/web/routers/chat.py src/perspicacite/mcp/server.py tests/unit/test_asb_response_metadata.py
git commit -m "feat(asb): response-time skill_metadata + workflow_metadata payloads"
```

---

### Task 12: End-to-end integration test — full pipeline against the fixture

**Files:**
- Modify: `tests/integration/test_asb_run_ingest_end_to_end.py` (add a real-Chroma test gated by env, plus the response-payload check)

- [ ] **Step 1: Add an unmocked end-to-end test against a temp Chroma**

```python
# extend tests/integration/test_asb_run_ingest_end_to_end.py

import os

import pytest


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("PERSPICACITE_E2E_ASB") != "1",
    reason="Set PERSPICACITE_E2E_ASB=1 to run the live ingest test",
)
async def test_ingest_asb_run_against_real_chroma(tmp_path):
    """Smoke test: full pipeline with the real chunker + Chroma,
    confirms the orchestrator stitches end-to-end without mocks.
    Gated so it doesn't run in CI by default (requires the embedding
    provider env variables)."""
    import shutil

    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(FIXTURE, target)

    result = await ingest_asb_run(
        asb_run_dir=str(target),
        kb_name="asb_e2e_test",
        include=("skills", "workflows"),
        mode="composite",
        update_skill_kb_json=True,
    )
    assert result["skills_ingested"] == 1
    assert result["workflows_ingested"] == 2
    assert result["total_chunks"] >= 3
    assert result["workflow_dag"]["nodes"]
```

- [ ] **Step 2: Add a response-payload regression test**

```python
def test_chat_response_includes_asb_blocks_when_chunks_match():
    """If the chat/MCP response builder receives chunks with
    content_kind=skill_* or workflow_card, the response carries
    skill_metadata / workflow_metadata blocks."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {"content_kind": "skill_body", "skill_id": "x",
                      "skill_name": "X", "tools": [], "environment": [], "parameters": []}},
        {"metadata": {"content_kind": "workflow_card", "task_id": "t1",
                      "task_card_title": "T1"}},
    ]
    out = build_asb_response_metadata(chunks)
    assert out["skill_metadata"]
    assert out["workflow_metadata"]
```

- [ ] **Step 3: Run the gated test (locally, with env set)**

Run: `PYTHONPATH=src PERSPICACITE_E2E_ASB=1 pytest tests/integration/test_asb_run_ingest_end_to_end.py::test_ingest_asb_run_against_real_chroma -v`
Expected: passes against a live Chroma + embedding stack. If the environment isn't configured (no embedding key), the test is skipped.

Run the unmocked unit-test slice anyway: `PYTHONPATH=src pytest tests/integration/test_asb_run_ingest_end_to_end.py -v`
Expected: 3 passed (2 mocked + 1 response).

- [ ] **Step 4: Run the full test suite**

Run: `PYTHONPATH=src pytest tests/unit tests/integration -q --tb=line | tail -5`
Expected: all green (existing 1316+ pass + new tests pass).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_asb_run_ingest_end_to_end.py
git commit -m "test(asb): live + response-payload integration coverage"
```

---

## Acceptance criteria

After all 12 tasks:

- [ ] `perspicacite ingest-asb-run ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/` runs to completion against a real ASB run, populates a composite KB with 28 skills + N cards + ~28 backing papers, and rewrites each `skill_kb.json` in place with a `perspicacite_ingest_completed=<ts>` stamp.
- [ ] A query routed to that KB returns chunks plus `skill_metadata[]` and (when relevant) `workflow_metadata[]` blocks in the response payload.
- [ ] `PYTHONPATH=src pytest tests/unit tests/integration -q` reports 0 failures (gated live tests skipped without env).
- [ ] The pinned WEB_SEARCH invariant test still passes (`test_paper_source_no_websearch_defaults.py`).
- [ ] `Paper.metadata` carries `content_kind` for every ASB-sourced Paper, distinguishing `skill_body` from `workflow_card`.

## Out of scope for this plan (consistent with the spec)

- Repo-fetching (cloning github URLs from `links.json[category=repo_github]`) — deferred to the parent skill-bundle ingest plan; this plan ships without it. `skill_kb.json.entries[]` will be `skill_body` + (later) `doi_paper` only until the github fetcher lands.
- ASB capsules under `capsules/{paper}__task_NNN/` (heavy per-task RO-Crate containers) — explicitly deferred to v2.
- Workflow DAG traversal as queryable graph nodes — DAG is stored as KB-level metadata and surfaced in the response, but edges are not indexed as chunks.
- Hosting an ASB MCP server in this repo. Perspicacité's MCP can federate to a paired ASB MCP when one exists.

## Effort summary

12 tasks, ~1150 LOC, 1-2 days end-to-end with subagent dispatch + two-stage review.
