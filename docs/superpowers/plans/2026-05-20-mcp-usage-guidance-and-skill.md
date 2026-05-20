# MCP Usage Guidance & Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **DO NOT commit anything in docs/superpowers/** — spec and plan are local working notes.

**Goal:** Make Perspicacité's MCP surface teach a calling LLM to use it well — via enriched docstrings, a `suggest_databases` helper, a live `get_usage_guide` tool, verified agentic mode, and a generic skill + ASB/Scriptorium playbooks.

**Architecture:** Three phases. Phase 1 (server, Perspicacite-AI) adds the two tools + docstrings + agentic verification. Phases 2–3 author markdown skills that reference Phase 1's tools. Phase 1 tasks all edit `mcp/server.py` so they run sequentially; skill tasks touch separate files/repos and run in parallel after Phase 1.

**Tech Stack:** Python, FastMCP (`@mcp.tool()`, `_json_ok`/`_json_error`, manual `_TOOL_NAMES` registration), pytest (`asyncio_mode=auto`, no `@pytest.mark.asyncio`). Markdown skills with YAML frontmatter.

**Repos & branches:**
- Perspicacite-AI `/Users/holobiomicslab/git/Perspicacite-AI` — branch `dev_v2b`
- AgenticScienceBuilder `/Users/holobiomicslab/git/AgenticScienceBuilder` — branch `feat/persp-passage-extraction-2026-05-20`
- Scriptorium `/Users/holobiomicslab/git/Scriptorium` — branch `feat/find-related-2026-05-20`

**Verified facts (from exploration):**
- `_TOOL_NAMES` is a manually-maintained list at `server.py:4713–4749`; every new tool must be added there.
- Tools use bare `@mcp.tool()`; responses use module-level `_json_ok(...)` → `{success: true, ...}` and `_json_error(...)`.
- `KNOWN_DATABASES` frozenset at `src/perspicacite/search/scilex_adapter.py:32–44` = {arxiv, crossref, pubmed, semantic_scholar, openalex, europepmc, ads, pubchem, inspire, dblp, google_scholar}. Imported locally inside tools (e.g. server.py:420, 1446).
- `generate_report` already accepts `mode` ∈ {basic, advanced, profound, agentic, literature_survey, contradiction} (server.py:1408–1416 → `RAGMode.*`, dispatched via `RAGEngine`). Unknown → advanced.
- Existing resource `perspicacite://info` at server.py:4752 returns `{"tools": _TOOL_NAMES, ...}`. Keep as-is.
- Skill conventions: Perspicacite-AI has `.claude/commands/` (no skills dir yet); Scriptorium uses `.claude/skills/{name}/SKILL.md` (frontmatter `name`+`description`) and `.claude/commands/*.md` (frontmatter `description`); ASB uses `.claude/commands/*.md` (`description`).

---

## Phase 1 — Server enablement (Perspicacite-AI, branch dev_v2b)

### Task 1: `suggest_databases` tool

**Files:**
- Create: `src/perspicacite/search/database_advisor.py` (pure, testable rule logic)
- Modify: `src/perspicacite/mcp/server.py` (new `@mcp.tool()` + add to `_TOOL_NAMES`)
- Test: `tests/unit/test_database_advisor.py` (logic) and add an MCP-tool test beside existing server tool tests (grep for where `search_literature`/`suggest`-style tools are tested)

- [ ] **Step 1: Read context.** Read `src/perspicacite/search/scilex_adapter.py:32–44` (KNOWN_DATABASES), a representative `@mcp.tool()` in server.py (e.g. `search_by_passage`) for the `_json_ok` pattern and how local imports are done, and the `_TOOL_NAMES` list at server.py:4713.

- [ ] **Step 2: Write failing test for the advisor logic.**

```python
# tests/unit/test_database_advisor.py
from perspicacite.search.database_advisor import suggest_databases_for_query
from perspicacite.search.scilex_adapter import KNOWN_DATABASES

def test_biomedical_query_recommends_pubmed():
    rec = suggest_databases_for_query("CRISPR gene therapy clinical trial outcomes")
    assert "pubmed" in rec.databases
    assert all(db in KNOWN_DATABASES for db in rec.databases)
    assert rec.reasoning

def test_cs_query_recommends_arxiv():
    rec = suggest_databases_for_query("transformer attention mechanism benchmark")
    assert "arxiv" in rec.databases

def test_chemistry_query_recommends_pubchem():
    rec = suggest_databases_for_query("solubility of aspirin compound synthesis")
    assert "pubchem" in rec.databases

def test_unknown_query_returns_broad_default():
    rec = suggest_databases_for_query("xyzzy")
    assert set(rec.databases) >= {"semantic_scholar", "openalex", "crossref"}

def test_all_recommended_are_known():
    rec = suggest_databases_for_query("anything at all")
    assert all(db in KNOWN_DATABASES for db in rec.databases)
```

- [ ] **Step 3: Run, confirm FAIL** (`cd /Users/holobiomicslab/git/Perspicacite-AI && source .venv/bin/activate && python -m pytest tests/unit/test_database_advisor.py -q`) — ModuleNotFound.

- [ ] **Step 4: Implement the advisor.**

```python
# src/perspicacite/search/database_advisor.py
from __future__ import annotations
from dataclasses import dataclass
from .scilex_adapter import KNOWN_DATABASES

_BROAD_DEFAULT = ["semantic_scholar", "openalex", "crossref"]

# keyword -> databases. First matching domain wins; results merged with broad default.
_DOMAIN_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("clinical", "patient", "gene", "genome", "protein", "cell", "disease",
      "drug", "therapy", "crispr", "biomed", "cancer", "rna", "dna", "vaccine"),
     ["pubmed", "europepmc"]),
    (("transformer", "neural", "algorithm", "benchmark", "machine learning",
      "deep learning", "attention", "dataset", "gpu", "quantum", "physics",
      "arxiv", "theorem", "manifold"),
     ["arxiv"]),
    (("compound", "molecule", "solubility", "synthesis", "reaction",
      "chemical", "cheminform", "smiles", "ligand"),
     ["pubchem"]),
    (("particle", "collider", "higgs", "quark", "lepton", "qcd",
      "high-energy", "hep"),
     ["inspire"]),
]

@dataclass(frozen=True)
class DatabaseSuggestion:
    databases: list[str]
    reasoning: str

def suggest_databases_for_query(query: str, hints: list[str] | None = None) -> DatabaseSuggestion:
    text = f"{query} {' '.join(hints or [])}".lower()
    chosen: list[str] = []
    reasons: list[str] = []
    for keywords, dbs in _DOMAIN_RULES:
        if any(k in text for k in keywords):
            for db in dbs:
                if db in KNOWN_DATABASES and db not in chosen:
                    chosen.append(db)
            reasons.append(f"matched {'/'.join(dbs)} on domain keywords")
    for db in _BROAD_DEFAULT:
        if db in KNOWN_DATABASES and db not in chosen:
            chosen.append(db)
    if not reasons:
        reasoning = "No domain-specific signal; returning broad general-purpose sources."
    else:
        reasoning = "; ".join(reasons) + "; plus broad general-purpose sources."
    return DatabaseSuggestion(databases=chosen, reasoning=reasoning)
```

- [ ] **Step 5: Run logic tests, confirm PASS.**

- [ ] **Step 6: Write failing MCP-tool test.** Find the existing server-tool test module (grep `def test_` + `suggest`/`search_literature` under `tests/`). Add a test that calls the `suggest_databases` MCP tool function and asserts the envelope:

```python
async def test_suggest_databases_tool_envelope():
    payload = await suggest_databases(query="CRISPR gene editing")
    assert payload["success"] is True
    assert "pubmed" in payload["recommended"]
    assert payload["reasoning"]
    assert set(payload["all_known"]) == set(KNOWN_DATABASES)
```

Import the tool the same way the existing tool tests import server tools.

- [ ] **Step 7: Run, confirm FAIL.**

- [ ] **Step 8: Implement the MCP tool in server.py** (place near other search tools; mirror an existing tool's structure):

```python
@mcp.tool()
async def suggest_databases(query: str, hints: list[str] | None = None) -> dict:
    """Recommend which literature databases to search for a query.

    Use this BEFORE search_literature / generate_report when you are unsure which
    `databases` to pass. Returns a recommended subset plus the full list of known
    databases so you can override. Deterministic topic heuristic — no LLM call.

    Returns: {success, recommended: [db...], reasoning: str, all_known: [db...]}.
    """
    from perspicacite.search.scilex_adapter import KNOWN_DATABASES
    from perspicacite.search.database_advisor import suggest_databases_for_query
    s = suggest_databases_for_query(query, hints)
    return _json_ok(recommended=s.databases, reasoning=s.reasoning,
                    all_known=sorted(KNOWN_DATABASES))
```

Confirm `_json_ok` accepts kwargs this way (check an existing call); adapt if it takes a dict.

- [ ] **Step 9: Add `"suggest_databases"` to `_TOOL_NAMES`** (server.py:4713 list).

- [ ] **Step 10: Run both test modules, confirm PASS.**

- [ ] **Step 11: Commit.**
```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
git add src/perspicacite/search/database_advisor.py src/perspicacite/mcp/server.py tests/unit/test_database_advisor.py tests/
git commit -m "feat(mcp): add suggest_databases tool with deterministic topic heuristic"
```

---

### Task 2: `get_usage_guide` tool + drift test

**Files:**
- Create: `src/perspicacite/mcp/usage_guide.py` (the guide data + builder)
- Modify: `src/perspicacite/mcp/server.py` (new `@mcp.tool()` + `_TOOL_NAMES`)
- Test: `tests/unit/test_usage_guide.py`

- [ ] **Step 1: Read context.** Re-read `_TOOL_NAMES` (now including `suggest_databases` from Task 1) and the `perspicacite://info` resource (server.py:4752). Skim the docstrings of the main tools to source accurate `purpose`/`when_to_use` text.

- [ ] **Step 2: Write failing tests.**

```python
# tests/unit/test_usage_guide.py
from perspicacite.mcp.usage_guide import build_usage_guide
from perspicacite.mcp.server import _TOOL_NAMES

def test_guide_has_core_sections():
    g = build_usage_guide()
    for key in ("capabilities", "decision_rules", "tools", "knob_defaults"):
        assert key in g
    assert isinstance(g["tools"], list) and g["tools"]

def test_guide_covers_every_registered_tool():
    g = build_usage_guide()
    documented = {t["name"] for t in g["tools"]}
    missing = set(_TOOL_NAMES) - documented
    assert not missing, f"tools missing from usage guide: {sorted(missing)}"

def test_each_tool_entry_has_required_fields():
    g = build_usage_guide()
    for t in g["tools"]:
        assert t["name"] and t["purpose"] and t["when_to_use"]
```

- [ ] **Step 3: Run, confirm FAIL.**

- [ ] **Step 4: Implement `build_usage_guide()`** in `usage_guide.py`. Define a module-level list of tool entries (`{name, purpose, when_to_use, key_knobs}`) covering EVERY name in `_TOOL_NAMES` (import the list and assert coverage in the builder is NOT needed — the test enforces it). Include `capabilities` (short bullets), `decision_rules` (the same procedure the skill teaches, as short strings), and `knob_defaults` (e.g. `{"optimize_query": "on for literature search", "screen_threshold": 0.0, "mode": "advanced"}`). Keep entries terse — this is a lookup, not prose.

To avoid drift burden, group rarely-distinct tools with a shared short `when_to_use`, but EVERY `_TOOL_NAMES` entry must appear by name.

- [ ] **Step 5: Run, confirm PASS.**

- [ ] **Step 6: Add MCP tool in server.py:**
```python
@mcp.tool()
async def get_usage_guide() -> dict:
    """Return the authoritative guide to using Perspicacité over MCP.

    Call this FIRST when planning multi-step research: it returns the capability
    summary, the decision rules (when to rephrase/translate/pick databases/mode/
    screening), a per-tool index (purpose + when to use + key knobs), and current
    knob defaults. Prefer this live guide over any cached assumptions.
    """
    from perspicacite.mcp.usage_guide import build_usage_guide
    return _json_ok(**build_usage_guide())
```

- [ ] **Step 7: Add `"get_usage_guide"` to `_TOOL_NAMES`.**

- [ ] **Step 8: Run `tests/unit/test_usage_guide.py` + the suggest_databases tests, confirm PASS.** The drift test now also guards `suggest_databases` and `get_usage_guide` themselves.

- [ ] **Step 9: Commit.**
```bash
git add src/perspicacite/mcp/usage_guide.py src/perspicacite/mcp/server.py tests/unit/test_usage_guide.py
git commit -m "feat(mcp): add get_usage_guide tool with tool-coverage drift test"
```

---

### Task 3: Docstring enrichment + agentic-mode verification

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (docstrings of `search_by_passage`, `get_relevant_passages`, `extract_parameters_from_passages`, `extract_failure_modes_from_passages`; add modes note to `generate_report` docstring)
- Test: `tests/unit/test_mcp_docstrings.py` (new); add agentic-mode routing test beside existing generate_report tests

- [ ] **Step 1: Read the four thin docstrings and the generate_report docstring + mode dispatch (server.py:1408–1416). Find existing generate_report tests (grep `generate_report` under tests/) and how they mock the engine.**

- [ ] **Step 2: Write failing docstring test.**

```python
# tests/unit/test_mcp_docstrings.py
import inspect
from perspicacite.mcp import server

REQUIRED = ["search_by_passage", "get_relevant_passages",
            "extract_parameters_from_passages", "extract_failure_modes_from_passages"]

def test_thin_tools_have_rich_docstrings():
    for name in REQUIRED:
        fn = getattr(server, name)
        doc = inspect.getdoc(fn) or ""
        assert len(doc) >= 200, f"{name} docstring too thin"
        assert "use" in doc.lower()  # has when-to-use guidance
```

- [ ] **Step 3: Run, confirm FAIL** (current docstrings are short).

- [ ] **Step 4: Enrich the four docstrings.** For each, structure: one-line purpose; "When to use (vs <nearest alternative>)"; key knobs with defaults; response shape. Be accurate to the actual params — read each signature first. Keep factual; no invented params.

- [ ] **Step 5: Run docstring test, confirm PASS.**

- [ ] **Step 6: Add `generate_report` modes documentation** — extend its docstring to enumerate the six modes (basic/advanced/profound/agentic/literature_survey/contradiction) with one line each and note `agentic` delegates to the multi-step orchestrator. No code change to dispatch.

- [ ] **Step 7: Write failing agentic-routing test.** Mirror existing generate_report tests' engine-mock approach. Assert that calling the tool with `mode="agentic"` constructs a `RAGRequest` whose mode resolves to `RAGMode.AGENTIC` (patch `RAGEngine`/`query_stream`, capture the request). If existing tests already cover other modes, copy that pattern exactly.

```python
async def test_generate_report_routes_agentic_mode(...):
    # patch RAGEngine to capture the RAGRequest; assert request.mode == RAGMode.AGENTIC
    ...
```

- [ ] **Step 8: Run, confirm it PASSES immediately if wiring already works** (expected — mode is already mapped). If it fails, the wiring has a real gap: fix the mode mapping minimally so `mode="agentic"` reaches the orchestrator, then re-run. Document the outcome in the commit message.

- [ ] **Step 9: Run `tests/unit/test_mcp_docstrings.py` + generate_report tests, confirm PASS.**

- [ ] **Step 10: Commit.**
```bash
git add src/perspicacite/mcp/server.py tests/
git commit -m "docs(mcp): enrich passage/extraction docstrings; document + verify generate_report modes"
```

---

## Phase 2 — Generic skill (Perspicacite-AI, branch dev_v2b)

### Task 4: `perspicacite-mcp` skill + CLAUDE.md pointer

**Files:**
- Create: `.claude/skills/perspicacite-mcp/SKILL.md`
- Modify: `CLAUDE.md` (one-line pointer)
- Test: `tests/unit/test_skill_frontmatter.py` (validates frontmatter)

- [ ] **Step 1: Read** Scriptorium's `.claude/skills/scriptorium-audit/SKILL.md` for the exact frontmatter + structure convention, and skim the four enriched docstrings + `build_usage_guide` content (Tasks 1–3) so the skill's decision table matches reality.

- [ ] **Step 2: Write failing frontmatter test.**

```python
# tests/unit/test_skill_frontmatter.py
from pathlib import Path

def test_perspicacite_mcp_skill_frontmatter():
    p = Path(".claude/skills/perspicacite-mcp/SKILL.md")
    assert p.exists()
    text = p.read_text()
    assert text.startswith("---")
    head = text.split("---", 2)[1]
    assert "name:" in head and "description:" in head
```

- [ ] **Step 3: Run, confirm FAIL.**

- [ ] **Step 4: Write the SKILL.md.** Frontmatter `name: perspicacite-mcp`, `description: ...`. Body sections (from the spec's Phase 2): (1) Translate non-English queries; (2) Query shaping + `optimize_query`; (3) Database selection via `suggest_databases`; (4) Tool-choice decision table; (5) Mode & screening; (6) Reading the `{success,...}`/`META`/`usage` envelope; (7) Call `get_usage_guide` for the live authoritative index. Keep it instructional and concise. Reference the actual tool names and knobs.

- [ ] **Step 5: Run frontmatter test, confirm PASS.**

- [ ] **Step 6: Add a one-line pointer in `CLAUDE.md`** under the MCP section: e.g. `For MCP usage, follow .claude/skills/perspicacite-mcp/SKILL.md (or call the get_usage_guide tool).` Place near the existing MCP mention; do not restructure the file.

- [ ] **Step 7: Commit.**
```bash
git add .claude/skills/perspicacite-mcp/SKILL.md CLAUDE.md tests/unit/test_skill_frontmatter.py
git commit -m "docs(skill): add perspicacite-mcp usage skill + CLAUDE.md pointer"
```

---

## Phase 3 — Consumer playbacks (parallel-safe: separate repos)

### Task 5: ASB playbook (AgenticScienceBuilder, branch feat/persp-passage-extraction-2026-05-20)

**Files:**
- Create: `.claude/commands/perspicacite-kb.md`

- [ ] **Step 1: Read** an existing ASB `.claude/commands/setup.md` for the `description`-only frontmatter convention, and `src/agentic_science_builder/perspicacite_client.py` for the real method names the playbook should reference (search_by_passage, get_relevant_passages, extract_parameters, extract_failure_modes, search_related_papers).

- [ ] **Step 2: Write the playbook.** Frontmatter `description: ...`. Body: how to build a skill-pack KB via Perspicacité MCP — `build_kb_from_search` → enrich parameters/failure modes via the extraction tools → provenance (`perspicacite_mcp`). One short decision section; then "see the perspicacite-mcp skill in the Perspicacite-AI repo for the general procedure." Keep terse.

- [ ] **Step 3: Sanity-check** the file starts with `---` and has `description:`. Commit.
```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder
git add .claude/commands/perspicacite-kb.md
git commit -m "docs(playbook): add perspicacite-kb command for MCP-driven KB building"
```

### Task 6: Scriptorium playbook (Scriptorium, branch feat/find-related-2026-05-20)

**Files:**
- Create: `.claude/skills/perspicacite-search/SKILL.md`

- [ ] **Step 1: Read** `Scriptorium/.claude/skills/scriptorium-audit/SKILL.md` for frontmatter/structure, the existing `.claude/commands/find-related.md`, and `scriptorium/literature/passage_search.py` for the real `find_related` entry point.

- [ ] **Step 2: Write the SKILL.md.** Frontmatter `name: perspicacite-search`, `description: ...`. Body: literature search while drafting — when to use `search_by_passage` for paragraph-level citation suggestions, the `/find-related` command, sentence vs keyword search choices. Reference the generic perspicacite-mcp skill for the broader procedure. Terse.

- [ ] **Step 3: Sanity-check** frontmatter has `name:` + `description:`. Commit.
```bash
cd /Users/holobiomicslab/git/Scriptorium
git add .claude/skills/perspicacite-search/SKILL.md
git commit -m "docs(skill): add perspicacite-search skill for drafting-time literature search"
```

---

## Final verification

- Perspicacite-AI: `cd /Users/holobiomicslab/git/Perspicacite-AI && source .venv/bin/activate && python -m pytest tests/unit/test_database_advisor.py tests/unit/test_usage_guide.py tests/unit/test_mcp_docstrings.py tests/unit/test_skill_frontmatter.py -q` — all green; plus a focused run of the MCP server test module to confirm no regression from `_TOOL_NAMES`/docstring edits.
- ASB + Scriptorium: confirm the new markdown files exist with valid frontmatter.
- Confirm nothing under `docs/superpowers/` is staged in any repo.
- Confirm `suggest_databases` and `get_usage_guide` both appear in `_TOOL_NAMES` and in `build_usage_guide()` (drift test green).
