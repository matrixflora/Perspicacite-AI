# Pre-existing Test Failure Fix-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 9 pre-existing unit-test failures in `tests/unit/` that have been documented across two audit sessions as mock-signature drift. All are test-only fixes; no production bug is being addressed.

**Architecture:** Each test file mocks a piece of production state (`app_state.config`, an `_ingest_files` function, an HTTP call) whose signature evolved while the test stayed pinned to the old shape. The fix in every case is to update the mock to match the current production signature — no production code changes.

**Tech Stack:** pytest, pytest-asyncio, unittest.mock, pydantic v2, httpx.

**Failure inventory (verified by `pytest` + source-read):**

| # | Test | Root cause | Fix |
|---|---|---|---|
| 1 | `test_arxiv_id_fallback.py::test_openalex_id_for_doi_arxiv_fallback` | Test asserts the *old* arXiv fallback API (`params={"filter": "ids.arxiv:..."}`); commit `9ad0baa` switched production to `{"id_list": ...}` + title.search via OpenAlex. Test is stale. | Update assertion at test:31 to expect `params.get("id_list")` |
| 2-3 | `test_local_docs_capsule_reader_route.py::test_non_capsule_paths_route_to_files`, `test_mixed_inputs_route_to_both` | Production `local_docs.py:233` now passes `external_metadata=` kwarg to `_ingest_files`; the test mock `fake_ingest_files` signatures predate this | Add `external_metadata=None` to all `fake_ingest_files` mock signatures (3 sites in the file: ~line 40, 65, 96) |
| 4-6 | `test_mcp_multi_kb_passthrough.py` (3 tests) | (a) `state.config = MagicMock()` returns MagicMock for `state.config.llm.default_provider` and `default_model`; `RAGRequest` validates these as `str` → pydantic ValidationError. (b) `_FakeDKB.search()` lacks the `filters=` kwarg that `mcp/server.py:553` now passes. | (a) Replace MagicMock config with `SimpleNamespace(llm=SimpleNamespace(default_provider="...", default_model="..."))` at ~line 46. (b) Add `filters=None` to `_FakeDKB.search()` signature at ~line 28. |
| 7 | `test_provenance_engine_wiring.py::test_mcp_generate_report_wires_provenance_and_message_id` | Same MagicMock-vs-pydantic issue as #4-6 (at ~line 45) | Same real-string SimpleNamespace fix |
| 8-9 | `test_zotero_ingest_worker.py` (2 tests) | Production `zotero_ingest.py:328` reads `app_state.config.capsule.auto_build_on_ingest`; test mock at ~line 75 lacks the `capsule` attribute on the config SimpleNamespace | Nest `capsule=SimpleNamespace(auto_build_on_ingest=False)` into the config mock |

**Out of scope:**
- Production changes (verified none are needed)
- Adding new test coverage (only restoring what was already there)
- Refactoring the tests beyond what's needed for the signature fix
- The legacy `models/papers.py:176` default param (separate concern)

**Commit strategy:** One commit per test file (5 commits total). Each commit is small, well-scoped, easy to revert if something turns out to be wrong about my diagnosis.

---

### Task 1: Fix `test_arxiv_id_fallback.py`

**Files:**
- Modify: `tests/unit/test_arxiv_id_fallback.py:31`

- [ ] **Step 1.1: Confirm production signature**

Read `src/perspicacite/pipeline/snowball.py` around the arXiv fallback (`_fetch_seed_work` or similar) to confirm the params passed to `httpx.AsyncClient.get` on the second call. Also read the test fixture at lines 14-40 to understand what flow it's mocking.

Expected finding: the arXiv API call now uses `params={"id_list": "<arxiv_id>"}`, and the test fixture currently asserts `params.get("filter") == "ids.arxiv:..."` which has not been a valid OpenAlex filter in this code path since `9ad0baa`.

- [ ] **Step 1.2: Run the failing test to confirm current red state**

Run: `PYTHONPATH=src pytest tests/unit/test_arxiv_id_fallback.py::test_openalex_id_for_doi_arxiv_fallback -v 2>&1 | tail -15`
Expected: FAIL with `assert params.get("filter") == "ids.arxiv:..."`.

- [ ] **Step 1.3: Update the assertion**

The test mocks two HTTP calls (the OpenAlex 404 + the arXiv fallback). Locate the section that asserts the second call's params and change the assertion to match the new shape.

If the test ASSERTS the wrong endpoint pattern (e.g., expects OpenAlex `/works?filter=` but production hits `arxiv.org/api/query?id_list=`), update both the URL check (if any) and the params check.

If after updating the assertions the test passes, you're done. If the test continues to fail because the production flow now involves THREE calls (OpenAlex 404 → arXiv title fetch → OpenAlex title.search), extend the mock to handle all three. The plan accepts either: minimal-fix-to-pass OR full-coverage rewrite, whichever is cleaner. Prefer the minimal fix.

- [ ] **Step 1.4: Run the test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_arxiv_id_fallback.py -v`
Expected: all tests in the file PASS.

- [ ] **Step 1.5: Commit**

```bash
git add tests/unit/test_arxiv_id_fallback.py
git commit -m "$(cat <<'EOF'
test(snowball): arxiv fallback assertion — id_list not filter

The arXiv-DOI fallback was refactored in 9ad0baa to fetch the arXiv
title (via export.arxiv.org id_list query) and then resolve to OpenAlex
via title.search. The test still asserted the old
``filter=ids.arxiv:...`` shape. Update the assertion to match the live
production flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Fix `test_local_docs_capsule_reader_route.py`

**Files:**
- Modify: `tests/unit/test_local_docs_capsule_reader_route.py` (3 `fake_ingest_files` signatures)

- [ ] **Step 2.1: Confirm production signature**

Read `src/perspicacite/integrations/local_docs.py` around line 233 to find the `_ingest_files(...)` call and confirm which kwargs it passes. Specifically: the test errors say `external_metadata` is unexpected, so confirm the production call site includes `external_metadata=...`.

- [ ] **Step 2.2: Run the failing tests to confirm current red state**

Run: `PYTHONPATH=src pytest tests/unit/test_local_docs_capsule_reader_route.py -v 2>&1 | tail -20`
Expected: 2 FAIL with `TypeError: ... got an unexpected keyword argument 'external_metadata'`. (One of the three tests in the file already passes — `test_capsule_only_paths_route_to_capsules`.)

- [ ] **Step 2.3: Add `external_metadata=None` to every `fake_ingest_files` mock signature**

There are three `fake_ingest_files` definitions in the file (one per test function). Each needs the kwarg added. Use Edit with enough surrounding context to disambiguate each.

The added kwarg should be `external_metadata=None` (default None — the tests don't care about its value, only that the signature accepts it).

- [ ] **Step 2.4: Run all 3 tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_local_docs_capsule_reader_route.py -v`
Expected: 3 PASSED.

- [ ] **Step 2.5: Commit**

```bash
git add tests/unit/test_local_docs_capsule_reader_route.py
git commit -m "$(cat <<'EOF'
test(local_docs): accept external_metadata kwarg in fake_ingest_files

Production local_docs.py:233 passes external_metadata=... into
_ingest_files; the test mocks predated that signature. Adding
external_metadata=None to the three mock signatures restores 2
previously-red tests to green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Fix `test_mcp_multi_kb_passthrough.py`

**Files:**
- Modify: `tests/unit/test_mcp_multi_kb_passthrough.py` (two distinct fixes; verify line numbers from the current source)

- [ ] **Step 3.1: Confirm production state**

Read:
- `src/perspicacite/mcp/server.py` around where `RAGRequest(...)` is constructed (search for `RAGRequest(` in the file) to confirm it reads `state.config.llm.default_provider` and `default_model` and that those are required `str` fields.
- The same `mcp/server.py` to find the call to `dkb.search(...)` that includes `filters=...` (the prior session traced this to ~line 553).
- The current `test_mcp_multi_kb_passthrough.py` to find:
  - The `state.config = MagicMock()` line (~46)
  - The `_FakeDKB.search` definition (~line 28)

- [ ] **Step 3.2: Run failing tests to confirm current red state**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_multi_kb_passthrough.py -v 2>&1 | tail -40`
Expected: 3 FAIL (the three named tests).

- [ ] **Step 3.3: Fix the MagicMock config**

Replace `state.config = MagicMock()` (or similar) with a SimpleNamespace that has the real string fields the production code needs:

```python
from types import SimpleNamespace

state.config = SimpleNamespace(
    llm=SimpleNamespace(
        default_provider="deepseek",
        default_model="deepseek-chat",
    ),
    # If the test exercises other state.config.* paths, add them here.
)
```

Use `inspect`/`grep` on the test to see what attributes the production code (during this test's setup) reads — adding only what's needed avoids overspecification.

- [ ] **Step 3.4: Fix the `_FakeDKB.search` signature**

Add `filters=None` to the `_FakeDKB.search` async-def signature. Example:

```python
async def search(self, query, top_k=5, filters=None):
    ...
```

- [ ] **Step 3.5: Run the 3 tests to verify all pass**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_multi_kb_passthrough.py -v`
Expected: all PASS.

- [ ] **Step 3.6: Commit**

```bash
git add tests/unit/test_mcp_multi_kb_passthrough.py
git commit -m "$(cat <<'EOF'
test(mcp): real string config + filters kwarg in _FakeDKB.search

Two mock-signature drifts in test_mcp_multi_kb_passthrough.py:
- state.config = MagicMock() made state.config.llm.default_provider /
  default_model unparseable by RAGRequest's str validator. Replace with
  a SimpleNamespace carrying real string defaults.
- mcp/server.py now passes filters= into dkb.search; _FakeDKB.search()
  must accept the kwarg.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fix `test_provenance_engine_wiring.py`

**Files:**
- Modify: `tests/unit/test_provenance_engine_wiring.py:~45`

- [ ] **Step 4.1: Confirm same root cause as Task 3**

Read the test to confirm it has the same `state.config = MagicMock()` issue around line 45.

- [ ] **Step 4.2: Run failing test**

Run: `PYTHONPATH=src pytest tests/unit/test_provenance_engine_wiring.py -v 2>&1 | tail -15`
Expected: FAIL with the pydantic ValidationError on provider/model.

- [ ] **Step 4.3: Apply the same SimpleNamespace fix as Task 3 Step 3.3**

Replace MagicMock config with a SimpleNamespace carrying real string defaults for `state.config.llm.default_provider` and `default_model`. Mirror Task 3's exact pattern so future readers see the convention.

- [ ] **Step 4.4: Run the test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_provenance_engine_wiring.py -v`
Expected: PASS.

- [ ] **Step 4.5: Commit**

```bash
git add tests/unit/test_provenance_engine_wiring.py
git commit -m "$(cat <<'EOF'
test(provenance): real string config to satisfy RAGRequest validation

Same root cause as the mcp_multi_kb_passthrough fix: MagicMock cannot
satisfy RAGRequest.provider / RAGRequest.model (str validators).
Replace state.config with a SimpleNamespace carrying real defaults.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Fix `test_zotero_ingest_worker.py`

**Files:**
- Modify: `tests/unit/test_zotero_ingest_worker.py:~75`

- [ ] **Step 5.1: Confirm production signature**

Read `src/perspicacite/integrations/zotero_ingest.py:328` to confirm it accesses `app_state.config.capsule.auto_build_on_ingest`. Note this is the same access pattern used by `kb.py` and `mcp/server.py` (the auto-capsule-build hook).

- [ ] **Step 5.2: Run failing tests**

Run: `PYTHONPATH=src pytest tests/unit/test_zotero_ingest_worker.py -v 2>&1 | tail -20`
Expected: 2 FAIL with `AttributeError: 'types.SimpleNamespace' object has no attribute 'capsule'`.

- [ ] **Step 5.3: Add `capsule` attr to the mock config**

In the existing `app_state = SimpleNamespace(config=SimpleNamespace(...))` mock (~line 75), nest:

```python
config=SimpleNamespace(
    pdf_download=None,
    capsule=SimpleNamespace(auto_build_on_ingest=False),
)
```

If the existing config SimpleNamespace already has other attributes (e.g. `pdf_download`), preserve them and add `capsule=` alongside.

- [ ] **Step 5.4: Run tests to verify both pass**

Run: `PYTHONPATH=src pytest tests/unit/test_zotero_ingest_worker.py -v`
Expected: all PASS.

- [ ] **Step 5.5: Commit**

```bash
git add tests/unit/test_zotero_ingest_worker.py
git commit -m "$(cat <<'EOF'
test(zotero): mock app_state.config.capsule for auto-build branch

zotero_ingest.py:328 reads app_state.config.capsule.auto_build_on_ingest;
the test mock predated that capsule auto-build feature. Add a
capsule=SimpleNamespace(auto_build_on_ingest=False) branch to the
existing config mock.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Verification sweep

**Files:** none (verification only).

- [ ] **Step 6.1: Run the full unit-test suite**

Run: `PYTHONPATH=src pytest tests/unit -q --tb=line 2>&1 | tail -20`
Expected: 0 failures (the 9 fixed + no regressions from the migration commits).

If anything still fails, investigate before declaring done. Any new failure means one of Tasks 1-5 broke something unexpected.

- [ ] **Step 6.2: No commit** — verification only.

---

## Self-review

**Spec coverage:**
- Task 1 → failure #1 ✓
- Task 2 → failures #2-3 ✓
- Task 3 → failures #4-6 ✓
- Task 4 → failure #7 ✓
- Task 5 → failures #8-9 ✓
- Task 6 → verification ✓

**Placeholder scan:** None.

**Type consistency:** Mock config replacements all use the same SimpleNamespace pattern; `external_metadata` and `filters` kwargs use `=None` defaults consistently.

**Caveats:**
- Task 1 may require more than a single assertion change if the test was originally written against a simpler flow than today's three-call chain. The plan permits "minimal fix or rewrite, prefer minimal."
- Some of the SimpleNamespace replacements may need more attrs than listed if other code paths in the test exercise other `state.config.*` fields. Add as needed.

---

## Execution handoff

This plan is small enough for inline execution but benefits from a fresh subagent per task (clean context per fix, no cross-contamination between mock patterns). Recommend Subagent-Driven.
