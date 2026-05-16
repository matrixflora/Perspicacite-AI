# Zotero MCP Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new MCP tools (`zotero_list_collections`, `zotero_get_collection_items`, `zotero_get_paper_resources`, `zotero_ingest_collection_to_kb`) that expose Perspicacité's Zotero read-path to ASB.

**Architecture:** Two new integration modules (`zotero_license.py` for license classification, `zotero_resources.py` for file-access ordering) back three read-only tools and one ingest trigger. All tools follow the existing `build_kbs_from_zotero` pattern: access `mcp_state` directly, return dicts, use `ZoteroClient` from `src/perspicacite/integrations/zotero.py`.

**Tech Stack:** Python 3.11+, httpx (already a dep), respx (test HTTP mocking), pytest-asyncio. No new runtime dependencies.

---

## File map

| Action | Path | Purpose |
|---|---|---|
| Create | `src/perspicacite/integrations/zotero_license.py` | `LicenseClassifier` — Crossref→OpenAlex→tags→heuristic; in-memory 7-day cache |
| Create | `src/perspicacite/integrations/zotero_resources.py` | `ResourceLocator` — builds ordered access list (local path first, then remote URLs) |
| Modify | `src/perspicacite/mcp/server.py` | Add 4 new `@mcp.tool()` functions after `build_kbs_from_zotero` (line 1520) |
| Create | `tests/unit/test_zotero_license.py` | Unit tests for LicenseClassifier (12+ SPDX assertions) |
| Create | `tests/unit/test_zotero_resources.py` | Unit tests for ResourceLocator (local-first, remote fallback) |
| Create | `tests/unit/test_zotero_mcp_new_tools.py` | Unit tests for all 4 MCP tools (error codes + valid shapes) |

---

## Task 1: LicenseClassifier

**Files:**
- Create: `src/perspicacite/integrations/zotero_license.py`
- Test: `tests/unit/test_zotero_license.py`

### Background

`LicenseClassifier` takes a DOI (and optionally a Zotero item dict for tag fallback) and returns a `LicenseInfo` dataclass. Resolution order: Crossref → OpenAlex → Zotero tags → heuristic.

SPDX → classification mapping:
- **permissive** (policy=`verbatim`): `CC0-1.0`, `CC-BY-*`, `CC-BY-SA-*`, `MIT`, `Apache-2.0`, `BSD-*`, `ISC`
- **closed** (policy=`reflavor`): `CC-BY-NC-*`, `CC-BY-ND-*`, no license, paywalled
- **unknown** (policy=`reflavor`): anything else (safe default)

- [ ] **Step 1.1: Write the failing tests**

```python
# tests/unit/test_zotero_license.py
"""Tests for LicenseClassifier."""
from __future__ import annotations
import pytest
from perspicacite.integrations.zotero_license import LicenseClassifier, LicenseInfo


def _clf() -> LicenseClassifier:
    return LicenseClassifier()


# --- classify_spdx unit tests (pure, no HTTP) ---

def test_cc0_is_permissive():
    info = _clf().classify_spdx("CC0-1.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_4_is_permissive():
    info = _clf().classify_spdx("CC-BY-4.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_sa_is_permissive():
    info = _clf().classify_spdx("CC-BY-SA-4.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_mit_is_permissive():
    info = _clf().classify_spdx("MIT")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_apache_is_permissive():
    info = _clf().classify_spdx("Apache-2.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_bsd3_is_permissive():
    info = _clf().classify_spdx("BSD-3-Clause")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_nc_is_closed():
    info = _clf().classify_spdx("CC-BY-NC-4.0")
    assert info.classification == "closed"
    assert info.policy == "reflavor"

def test_cc_by_nd_is_closed():
    info = _clf().classify_spdx("CC-BY-ND-4.0")
    assert info.classification == "closed"
    assert info.policy == "reflavor"

def test_none_spdx_is_unknown():
    info = _clf().classify_spdx(None)
    assert info.classification == "unknown"
    assert info.policy == "reflavor"

def test_unknown_spdx_is_unknown():
    info = _clf().classify_spdx("LicenseRef-Proprietary")
    assert info.classification == "unknown"
    assert info.policy == "reflavor"

def test_classify_url_cc_by():
    info = _clf().classify_url("https://creativecommons.org/licenses/by/4.0/")
    assert info.classification == "permissive"

def test_classify_url_cc_by_nc():
    info = _clf().classify_url("https://creativecommons.org/licenses/by-nc/4.0/")
    assert info.classification == "closed"

def test_classify_zotero_tag_open_access():
    item = {"data": {"tags": [{"tag": "open-access"}, {"tag": "metabolomics"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is not None
    assert info.classification == "permissive"
    assert info.source == "zotero_tag"

def test_classify_zotero_tag_closed():
    item = {"data": {"tags": [{"tag": "closed"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is not None
    assert info.classification == "closed"

def test_classify_zotero_tag_none_when_no_known_tag():
    item = {"data": {"tags": [{"tag": "metabolomics"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is None  # no license tag → can't classify

def test_heuristic_is_oa_true_returns_permissive():
    info = _clf().heuristic(is_oa=True)
    assert info.classification == "permissive"
    assert info.source == "heuristic"

def test_heuristic_is_oa_false_returns_unknown():
    info = _clf().heuristic(is_oa=False)
    assert info.classification == "unknown"

def test_cache_hit():
    clf = _clf()
    info1 = clf.classify_spdx("CC-BY-4.0")
    clf._cache["10.9999/test"] = (info1, 9999999999)  # expires far future
    info2 = clf.get_cached("10.9999/test")
    assert info2 is info1
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI/.claude/worktrees/goofy-chatterjee-539fcf
PYTHONPATH=src pytest tests/unit/test_zotero_license.py -x -q 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'perspicacite.integrations.zotero_license'`

- [ ] **Step 1.3: Implement `zotero_license.py`**

```python
# src/perspicacite/integrations/zotero_license.py
"""License classification for Zotero items.

Resolution chain: Crossref → OpenAlex → Zotero tags → heuristic.
Results cached in-memory per DOI with 7-day TTL.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

_PERMISSIVE_SPDX_PREFIXES = (
    "CC0",
    "CC-BY-4", "CC-BY-3", "CC-BY-2", "CC-BY-1",
    "CC-BY-SA",
    "MIT",
    "Apache-2.0",
    "BSD-",
    "ISC",
)
_CLOSED_SPDX_PATTERNS = ("CC-BY-NC", "CC-BY-ND")

_CC_URL_SLUG_RE = re.compile(
    r"creativecommons\.org/licenses/([a-z0-9\-]+)/",
    re.I,
)
_CC_PD_RE = re.compile(r"creativecommons\.org/publicdomain/zero/", re.I)

_KNOWN_OPEN_TAGS = {"open-access", "open access", "cc-by", "cc0"}
_KNOWN_CLOSED_TAGS = {"closed", "paywalled", "restricted"}

_SEVEN_DAYS = 7 * 24 * 3600


@dataclass
class LicenseInfo:
    spdx: str | None
    classification: str   # "permissive" | "closed" | "unknown"
    policy: str           # "verbatim" | "reflavor"
    source: str           # "crossref" | "openalex" | "zotero_tag" | "heuristic" | "unknown"


def _url_to_spdx(url: str) -> str | None:
    """Best-effort conversion of a license URL to an SPDX identifier."""
    if _CC_PD_RE.search(url):
        return "CC0-1.0"
    m = _CC_URL_SLUG_RE.search(url)
    if m:
        slug = m.group(1).upper().replace("-", "-")
        return f"CC-BY-{slug}" if slug != "BY" else "CC-BY-4.0"
    if "opensource.org/licenses/MIT" in url or url.lower().endswith("/mit"):
        return "MIT"
    if "apache.org/licenses/LICENSE-2.0" in url:
        return "Apache-2.0"
    return None


class LicenseClassifier:
    """Classify a paper's license from DOI or Zotero item metadata."""

    def __init__(self) -> None:
        # {doi: (LicenseInfo, expire_timestamp)}
        self._cache: dict[str, tuple[LicenseInfo, float]] = {}

    # ------------------------------------------------------------------
    # Public sync helpers (pure, no I/O — for testing and tag resolution)
    # ------------------------------------------------------------------

    def classify_spdx(self, spdx: str | None) -> LicenseInfo:
        """Classify a known SPDX identifier string."""
        if not spdx:
            return LicenseInfo(spdx=None, classification="unknown", policy="reflavor", source="unknown")
        upper = spdx.upper()
        for pat in _CLOSED_SPDX_PATTERNS:
            if upper.startswith(pat):
                return LicenseInfo(spdx=spdx, classification="closed", policy="reflavor", source="spdx")
        for prefix in _PERMISSIVE_SPDX_PREFIXES:
            if upper.startswith(prefix.upper()):
                return LicenseInfo(spdx=spdx, classification="permissive", policy="verbatim", source="spdx")
        return LicenseInfo(spdx=spdx, classification="unknown", policy="reflavor", source="unknown")

    def classify_url(self, url: str) -> LicenseInfo:
        """Classify from a license URL (e.g. from Crossref)."""
        spdx = _url_to_spdx(url)
        info = self.classify_spdx(spdx)
        info.source = "url"
        return info

    def classify_zotero_tags(self, zotero_item: dict) -> LicenseInfo | None:
        """Return LicenseInfo if any known license tag is present; else None."""
        tags = [
            (t.get("tag") or "").lower().strip()
            for t in ((zotero_item.get("data") or {}).get("tags") or [])
        ]
        for tag in tags:
            if tag in _KNOWN_OPEN_TAGS or tag.startswith("cc-by"):
                return LicenseInfo(spdx=None, classification="permissive", policy="verbatim", source="zotero_tag")
            if tag in _KNOWN_CLOSED_TAGS:
                return LicenseInfo(spdx=None, classification="closed", policy="reflavor", source="zotero_tag")
        return None

    def heuristic(self, *, is_oa: bool) -> LicenseInfo:
        """Low-confidence guess when no explicit license is known."""
        if is_oa:
            return LicenseInfo(spdx=None, classification="permissive", policy="verbatim", source="heuristic")
        return LicenseInfo(spdx=None, classification="unknown", policy="reflavor", source="heuristic")

    def get_cached(self, doi: str) -> LicenseInfo | None:
        entry = self._cache.get(doi)
        if entry is None:
            return None
        info, expires = entry
        if time.time() > expires:
            del self._cache[doi]
            return None
        return info

    def _store(self, doi: str, info: LicenseInfo) -> LicenseInfo:
        self._cache[doi] = (info, time.time() + _SEVEN_DAYS)
        return info

    # ------------------------------------------------------------------
    # Async resolution (I/O)
    # ------------------------------------------------------------------

    async def classify(
        self,
        doi: str,
        *,
        zotero_item: dict | None = None,
        http_client: httpx.AsyncClient | None = None,
        unpaywall_email: str = "perspicacite@example.com",
    ) -> LicenseInfo:
        """Full resolution chain. Returns cached result when available."""
        cached = self.get_cached(doi)
        if cached is not None:
            return cached

        client = http_client or httpx.AsyncClient()
        close_client = http_client is None

        try:
            # 1. Crossref
            info = await self._from_crossref(doi, client)
            if info is not None:
                return self._store(doi, info)

            # 2. OpenAlex
            info, is_oa = await self._from_openalex(doi, client)
            if info is not None:
                return self._store(doi, info)

            # 3. Zotero tags
            if zotero_item is not None:
                info = self.classify_zotero_tags(zotero_item)
                if info is not None:
                    return self._store(doi, info)

            # 4. Heuristic
            return self._store(doi, self.heuristic(is_oa=is_oa or False))

        except Exception:
            # Never crash the caller over a license lookup failure.
            return LicenseInfo(spdx=None, classification="unknown", policy="reflavor", source="unknown")
        finally:
            if close_client:
                await client.aclose()

    async def _from_crossref(
        self, doi: str, client: httpx.AsyncClient
    ) -> LicenseInfo | None:
        try:
            r = await client.get(
                f"https://api.crossref.org/works/{doi}",
                timeout=8.0,
                headers={"User-Agent": "Perspicacite/1.0 (mailto:perspicacite@example.com)"},
            )
            if r.status_code != 200:
                return None
            licenses = (r.json().get("message") or {}).get("license") or []
            for lic in licenses:
                url = lic.get("URL") or ""
                if url:
                    info = self.classify_url(url)
                    info.source = "crossref"
                    if info.classification != "unknown":
                        return info
        except Exception:
            pass
        return None

    async def _from_openalex(
        self, doi: str, client: httpx.AsyncClient
    ) -> tuple[LicenseInfo | None, bool]:
        """Returns (LicenseInfo | None, is_oa: bool)."""
        try:
            r = await client.get(
                f"https://api.openalex.org/works/https://doi.org/{doi}",
                timeout=8.0,
            )
            if r.status_code != 200:
                return None, False
            body = r.json()
            oa = body.get("open_access") or {}
            is_oa = bool(oa.get("is_oa"))
            lic = oa.get("license") or ""
            if lic:
                info = self.classify_spdx(lic)
                info.source = "openalex"
                if info.classification != "unknown":
                    return info, is_oa
            return None, is_oa
        except Exception:
            return None, False
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_license.py -v 2>&1 | tail -25
```

Expected: All tests pass (18 total).

- [ ] **Step 1.5: Commit**

```bash
git add src/perspicacite/integrations/zotero_license.py tests/unit/test_zotero_license.py
git commit -m "$(cat <<'EOF'
feat(zotero): add LicenseClassifier with Crossref→OpenAlex→tag resolution chain

Returns LicenseInfo(spdx, classification, policy, source) with 7-day
in-memory cache. Pure classify_spdx/classify_url/classify_zotero_tags
helpers are side-effect-free for easy testing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: ResourceLocator

**Files:**
- Create: `src/perspicacite/integrations/zotero_resources.py`
- Test: `tests/unit/test_zotero_resources.py`

### Background

`ResourceLocator` takes a DOI, a Zotero item dict, the app config, and the list of
Zotero attachment dicts (from `ZoteroClient.get_item_attachments()`). It returns a
list of `Resource` objects, each with a `role` (`fulltext_pdf`/`supplementary`/`note`),
`filename`, and an ordered `access` list (local paths first, then remote URLs).

Local paths come from:
- PDF: `cached_pdf_path(doi, config.pdf_download.cache_dir)`
- SI files: `Path(config.capsule.root) / _sanitize_paper_id(doi) / "supplementary" / "files" / *`

Remote URLs are added in priority order:
1. Unpaywall (`https://api.unpaywall.org/v2/{doi}?email={email}`) for OA PDF
2. Publisher URL from Zotero attachment (`data.url`)
3. DOI resolver (`https://doi.org/{doi}`)

- [ ] **Step 2.1: Write the failing tests**

```python
# tests/unit/test_zotero_resources.py
"""Tests for ResourceLocator."""
from __future__ import annotations
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from perspicacite.integrations.zotero_resources import ResourceLocator, Resource


def _fake_config(tmp_path: Path) -> SimpleNamespace:
    cache_dir = tmp_path / "pdfs"
    cache_dir.mkdir()
    capsule_root = tmp_path / "capsules"
    capsule_root.mkdir()
    return SimpleNamespace(
        pdf_download=SimpleNamespace(
            cache_pdfs=True,
            cache_dir=str(cache_dir),
            unpaywall_email="test@example.com",
        ),
        capsule=SimpleNamespace(root=str(capsule_root)),
    )


def test_local_pdf_first_when_cached(tmp_path):
    cfg = _fake_config(tmp_path)
    doi = "10.1234/test"
    # Write a fake cached PDF
    pdf_path = Path(cfg.pdf_download.cache_dir) / "10.1234_test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4" + b"\x00" * 2000)

    rl = ResourceLocator(cfg)
    zotero_item = {"key": "ABC", "data": {"DOI": doi, "itemType": "journalArticle"}}
    resources = rl.build(doi=doi, zotero_item=zotero_item, attachments=[])

    pdf_resources = [r for r in resources if r["role"] == "fulltext_pdf"]
    assert len(pdf_resources) == 1
    access = pdf_resources[0]["access"]
    assert access[0]["type"] == "local"
    assert "10.1234_test.pdf" in access[0]["path"]


def test_remote_doi_resolver_always_present(tmp_path):
    cfg = _fake_config(tmp_path)
    doi = "10.1234/nopdf"
    rl = ResourceLocator(cfg)
    zotero_item = {"key": "ABC", "data": {"DOI": doi}}
    resources = rl.build(doi=doi, zotero_item=zotero_item, attachments=[])
    pdf_resources = [r for r in resources if r["role"] == "fulltext_pdf"]
    assert len(pdf_resources) == 1
    vias = [a["via"] for a in pdf_resources[0]["access"] if a["type"] == "remote"]
    assert "doi_resolver" in vias


def test_si_files_included_when_on_disk(tmp_path):
    cfg = _fake_config(tmp_path)
    doi = "10.1234/withsi"
    # Simulate capsule SI directory
    si_dir = Path(cfg.capsule.root) / "10.1234__withsi" / "supplementary" / "files"
    si_dir.mkdir(parents=True)
    (si_dir / "table_S1.xlsx").write_bytes(b"fake excel content")

    rl = ResourceLocator(cfg)
    zotero_item = {"key": "ABC", "data": {"DOI": doi}}
    resources = rl.build(doi=doi, zotero_item=zotero_item, attachments=[])
    si_resources = [r for r in resources if r["role"] == "supplementary"]
    assert len(si_resources) == 1
    assert si_resources[0]["filename"] == "table_S1.xlsx"
    assert si_resources[0]["access"][0]["type"] == "local"


def test_publisher_url_from_zotero_attachment(tmp_path):
    cfg = _fake_config(tmp_path)
    doi = "10.1234/pub"
    attachments = [{
        "key": "ATT1",
        "data": {
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "linked_url",
            "url": "https://publisher.com/pdf/article.pdf",
            "title": "article.pdf",
        }
    }]
    rl = ResourceLocator(cfg)
    zotero_item = {"key": "ABC", "data": {"DOI": doi}}
    resources = rl.build(doi=doi, zotero_item=zotero_item, attachments=attachments)
    pdf_resources = [r for r in resources if r["role"] == "fulltext_pdf"]
    vias = [a.get("via") for a in pdf_resources[0]["access"] if a["type"] == "remote"]
    assert "publisher" in vias


def test_no_duplicate_doi_resolver_when_publisher_present(tmp_path):
    cfg = _fake_config(tmp_path)
    doi = "10.1234/dup"
    attachments = [{
        "key": "ATT1",
        "data": {
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "linked_url",
            "url": "https://pub.com/pdf.pdf",
        }
    }]
    rl = ResourceLocator(cfg)
    resources = rl.build(doi=doi, zotero_item={"key": "X", "data": {"DOI": doi}}, attachments=attachments)
    pdf = [r for r in resources if r["role"] == "fulltext_pdf"][0]
    resolver_count = sum(1 for a in pdf["access"] if a.get("via") == "doi_resolver")
    assert resolver_count == 1  # appears exactly once
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_resources.py -x -q 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'perspicacite.integrations.zotero_resources'`

- [ ] **Step 2.3: Implement `zotero_resources.py`**

```python
# src/perspicacite/integrations/zotero_resources.py
"""ResourceLocator: ordered file access lists for Zotero items.

Returns local paths (verified on disk) before remote URLs.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _sanitize_capsule_id(doi: str) -> str:
    """Mirror the capsule_builder sanitisation: ':' → '_', '/' → '__'."""
    clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    return clean.replace(":", "_").replace("/", "__")


class ResourceLocator:
    """Build ordered access lists for a paper's files.

    Call ``build(doi, zotero_item, attachments)`` to get a list of resource
    dicts matching the zotero_get_paper_resources response schema.
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        doi: str,
        zotero_item: dict[str, Any],
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []

        # --- Full-text PDF ---
        pdf_access = self._pdf_access(doi, attachments)
        resources.append({
            "role": "fulltext_pdf",
            "filename": "paper.pdf",
            "access": pdf_access,
        })

        # --- Supplementary files ---
        for si in self._si_files(doi):
            resources.append(si)

        return resources

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pdf_access(
        self, doi: str, attachments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        access: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        # 1. Local PDF cache
        local = self._local_pdf_path(doi)
        if local is not None:
            access.append({"type": "local", "path": str(local)})

        # 2. Publisher URL from Zotero attachments (linked_url or imported_url PDFs)
        for att in attachments:
            data = att.get("data") or {}
            ct = data.get("contentType") or ""
            lm = data.get("linkMode") or ""
            url = data.get("url") or ""
            if "pdf" in ct.lower() and url and url not in seen_urls:
                access.append({"type": "remote", "url": url, "via": "publisher"})
                seen_urls.add(url)

        # 3. DOI resolver (always last, de-dup)
        doi_url = f"https://doi.org/{doi}"
        if doi_url not in seen_urls:
            access.append({"type": "remote", "url": doi_url, "via": "doi_resolver"})

        return access

    def _local_pdf_path(self, doi: str) -> Path | None:
        pdf_cfg = getattr(self._config, "pdf_download", None)
        if pdf_cfg is None or not getattr(pdf_cfg, "cache_pdfs", False):
            return None
        cache_dir = getattr(pdf_cfg, "cache_dir", None)
        if not cache_dir:
            return None
        from perspicacite.pipeline.download.pdf_cache import cached_pdf_path
        return cached_pdf_path(doi, cache_dir)

    def _si_files(self, doi: str) -> list[dict[str, Any]]:
        cap_cfg = getattr(self._config, "capsule", None)
        if cap_cfg is None:
            return []
        root = getattr(cap_cfg, "root", None)
        if not root:
            return []
        paper_id = _sanitize_capsule_id(doi)
        si_dir = Path(root) / paper_id / "supplementary" / "files"
        if not si_dir.is_dir():
            return []
        result = []
        for f in sorted(si_dir.iterdir()):
            if f.is_file():
                result.append({
                    "role": "supplementary",
                    "filename": f.name,
                    "access": [{"type": "local", "path": str(f)}],
                })
        return result
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_resources.py -v 2>&1 | tail -15
```

Expected: All 5 tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add src/perspicacite/integrations/zotero_resources.py tests/unit/test_zotero_resources.py
git commit -m "$(cat <<'EOF'
feat(zotero): add ResourceLocator — ordered file access lists (local-first)

Checks local PDF cache and capsule SI directory before adding remote
URLs (publisher attachment URL → DOI resolver). No network I/O.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `zotero_list_collections` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (append after line 1520, after `build_kbs_from_zotero`)
- Test: `tests/unit/test_zotero_mcp_new_tools.py`

### Background

The Zotero `list_collections()` API returns a flat list. Each item has:
```json
{"key": "ABC", "data": {"name": "Metabolomics", "parentCollection": false}}
```
`parentCollection` is `false` (not `null`) for top-level, or a key string for sub-collections.
We build a nested tree by recursively grouping by parent.

Item counts require a separate API call per collection — this implementation sets
`item_count: null` (too expensive to compute for every collection on every call;
callers should use `zotero_get_collection_items` for exact counts).

- [ ] **Step 3.1: Write the failing test**

Create `tests/unit/test_zotero_mcp_new_tools.py` with this initial section:

```python
# tests/unit/test_zotero_mcp_new_tools.py
"""Unit tests for the 4 new Zotero MCP tools."""
from __future__ import annotations
from types import SimpleNamespace

import pytest
from perspicacite.mcp import server as mcp_server


def _zotero_cfg(enabled=True, api_key="k", library_id="42"):
    return SimpleNamespace(
        enabled=enabled, api_key=api_key, library_id=library_id,
        library_type="user", collection_key="", base_url="",
    )


def _fake_state(zotero_cfg=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            zotero=zotero_cfg or _zotero_cfg(),
            pdf_download=SimpleNamespace(cache_pdfs=False, cache_dir="", unpaywall_email=""),
            capsule=SimpleNamespace(root="./data/capsules"),
        ),
        job_registry=None,
    )


def _unwrap(fn):
    return fn.fn if hasattr(fn, "fn") else fn


# --- zotero_list_collections ---

@pytest.mark.asyncio
async def test_list_collections_not_configured(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(enabled=False)))
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_list_collections_no_api_key(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(api_key="")))
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_list_collections_auth_failed(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod

    async def _bad_paginated(self, path, params=None):
        raise httpx.HTTPStatusError(
            "403", request=httpx.Request("GET", "http://x"), response=httpx.Response(403)
        )

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _bad_paginated)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_AUTH_FAILED"


@pytest.mark.asyncio
async def test_list_collections_returns_tree(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _fake_paginated(self, path, params=None):
        return [
            {"key": "AAA", "data": {"name": "Top", "parentCollection": False}},
            {"key": "BBB", "data": {"name": "Sub", "parentCollection": "AAA"}},
        ]

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _fake_paginated)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert "collections" in out
    assert len(out["collections"]) == 1  # only top-level
    assert out["collections"][0]["id"] == "AAA"
    assert out["collections"][0]["subcollections"][0]["id"] == "BBB"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py::test_list_collections_not_configured -x -q 2>&1 | head -15
```

Expected: `AttributeError: module 'perspicacite.mcp.server' has no attribute 'zotero_list_collections'`

- [ ] **Step 3.3: Add `zotero_list_collections` to `server.py`**

Open `src/perspicacite/mcp/server.py`. After the closing line of `build_kbs_from_zotero` (around line 1520), add:

```python
# =============================================================================
# Tool 13: zotero_list_collections
# =============================================================================

_zotero_collections_cache: dict[str, tuple[list, float]] = {}
_COLLECTION_CACHE_TTL = 3600.0  # 1 hour


def _build_collection_tree(
    flat: list[dict], parent_key: str | None = None
) -> list[dict]:
    result = []
    for coll in flat:
        data = coll.get("data") or {}
        pc = data.get("parentCollection")
        coll_parent = None if (pc is False or not pc) else pc
        if coll_parent == parent_key:
            result.append({
                "id": coll["key"],
                "name": data.get("name") or "",
                "parent_id": parent_key,
                "item_count": None,  # expensive to compute; use zotero_get_collection_items
                "subcollections": _build_collection_tree(flat, parent_key=coll["key"]),
            })
    return result


@mcp.tool()
async def zotero_list_collections(
    library_id: str | None = None,
    include_subcollections: bool = True,
) -> dict:
    """List all Zotero collections (with sub-collection tree).

    Args:
        library_id: Override the configured library_id for this call.
        include_subcollections: If True (default), return a nested tree.
            If False, return only top-level collections.

    Returns:
        {"collections": [...], "library_id": str, "library_type": str}
        Each collection: {"id", "name", "parent_id", "item_count", "subcollections"}
    """
    import time
    import httpx
    from perspicacite.integrations.zotero import ZoteroClient

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key):
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "Zotero not enabled or api_key missing"}

    eff_library_id = library_id or cfg.library_id
    if not eff_library_id:
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "library_id required"}

    base_url = getattr(cfg, "base_url", "") or None

    # 1-hour in-memory cache keyed on (library_id, library_type)
    cache_key = f"{eff_library_id}:{cfg.library_type}"
    cached = _zotero_collections_cache.get(cache_key)
    if cached and time.time() < cached[1]:
        flat = cached[0]
    else:
        client = ZoteroClient(
            api_key=cfg.api_key,
            library_id=eff_library_id,
            library_type=cfg.library_type,
            base_url=base_url,
        )
        try:
            flat = await client.list_collections()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403:
                return {"error": "ZOTERO_AUTH_FAILED", "message": "Zotero API returned 403"}
            if status == 429:
                ra = exc.response.headers.get("retry-after") or "60"
                return {"error": "ZOTERO_RATE_LIMITED", "retry_after_s": float(ra)}
            if status == 404:
                return {"error": "LIBRARY_NOT_FOUND", "message": f"Library {eff_library_id} not found"}
            return {"error": "ZOTERO_ERROR", "message": str(exc)}
        _zotero_collections_cache[cache_key] = (flat, time.time() + _COLLECTION_CACHE_TTL)

    if include_subcollections:
        collections = _build_collection_tree(flat, parent_key=None)
    else:
        collections = [
            {"id": c["key"], "name": (c.get("data") or {}).get("name") or "",
             "parent_id": None, "item_count": None, "subcollections": []}
            for c in flat
            if not (c.get("data") or {}).get("parentCollection")
        ]

    return {
        "collections": collections,
        "library_id": eff_library_id,
        "library_type": cfg.library_type,
    }
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "list_collections" -v 2>&1 | tail -15
```

Expected: All 4 `list_collections` tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_zotero_mcp_new_tools.py
git commit -m "$(cat <<'EOF'
feat(mcp): add zotero_list_collections tool with 1-hour collection tree cache

Returns nested collection tree; handles ZOTERO_NOT_CONFIGURED,
ZOTERO_AUTH_FAILED, ZOTERO_RATE_LIMITED, LIBRARY_NOT_FOUND.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `zotero_get_collection_items` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Modify: `tests/unit/test_zotero_mcp_new_tools.py`

### Background

Wraps `ZoteroClient.list_items_in_collection()` with cursor-based pagination and per-item license classification. Cursor is a base64-encoded start offset. License classification is run concurrently (per item, with a shared `httpx.AsyncClient`).

A Zotero item dict looks like:
```json
{"key": "WXYZ9876", "data": {"DOI": "10.1234/...", "title": "...", "creators": [...], "date": "2025", "abstractNote": "...", "itemType": "journalArticle", "tags": [{"tag": "open-access"}]}}
```

- [ ] **Step 4.1: Add tests for `zotero_get_collection_items`**

Append to `tests/unit/test_zotero_mcp_new_tools.py`:

```python
# --- zotero_get_collection_items ---

@pytest.mark.asyncio
async def test_get_collection_items_collection_not_found(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod

    async def _bad(self, path, params=None):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
        )

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _bad)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_get_collection_items)(collection_id="MISSING")
    assert out["error"] == "COLLECTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_collection_items_returns_items_with_license(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod

    async def _fake_items(self, coll_key, *, include_subcollections=True):
        return [{
            "key": "ITEM1",
            "data": {
                "DOI": "10.1234/open",
                "title": "Open Paper",
                "creators": [{"firstName": "A", "lastName": "Smith"}],
                "date": "2024",
                "abstractNote": "Abstract text",
                "itemType": "journalArticle",
                "tags": [{"tag": "open-access"}],
            }
        }]

    async def _fake_classify(self, doi, *, zotero_item=None, http_client=None, **kw):
        from perspicacite.integrations.zotero_license import LicenseInfo
        return LicenseInfo(spdx="CC-BY-4.0", classification="permissive", policy="verbatim", source="crossref")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "list_items_in_collection", _fake_items)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    out = await _unwrap(mcp_server.zotero_get_collection_items)(collection_id="AAA")
    assert "items" in out
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert item["doi"] == "10.1234/open"
    assert item["license"]["classification"] == "permissive"
    assert item["license"]["policy"] == "verbatim"
    assert item["has_attachments"] is False
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_collection_items_cursor_pagination(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod
    from perspicacite.integrations.zotero_license import LicenseInfo

    # 3 items, limit=2 → first page has 2, cursor to get 3rd
    all_items = [
        {"key": f"I{i}", "data": {"DOI": f"10.0/{i}", "title": f"T{i}", "creators": [],
          "date": "2024", "abstractNote": "", "itemType": "journalArticle", "tags": []}}
        for i in range(3)
    ]

    async def _fake_items(self, coll_key, *, include_subcollections=True):
        return all_items

    async def _fake_classify(self, doi, **kw):
        return LicenseInfo(spdx=None, classification="unknown", policy="reflavor", source="unknown")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "list_items_in_collection", _fake_items)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    page1 = await _unwrap(mcp_server.zotero_get_collection_items)(
        collection_id="AAA", limit=2
    )
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None

    page2 = await _unwrap(mcp_server.zotero_get_collection_items)(
        collection_id="AAA", limit=2, cursor=page1["next_cursor"]
    )
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "get_collection_items" -x -q 2>&1 | head -10
```

Expected: `AttributeError: module ... has no attribute 'zotero_get_collection_items'`

- [ ] **Step 4.3: Add `zotero_get_collection_items` to `server.py`**

Append after `zotero_list_collections` in `server.py`:

```python
# =============================================================================
# Tool 14: zotero_get_collection_items
# =============================================================================

import base64 as _base64


def _encode_cursor(start: int) -> str:
    return _base64.b64encode(str(start).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        return int(_base64.b64decode(cursor.encode()).decode())
    except Exception:
        return 0


@mcp.tool()
async def zotero_get_collection_items(
    collection_id: str,
    library_id: str | None = None,
    include_abstract: bool = True,
    limit: int = 200,
    cursor: str | None = None,
) -> dict:
    """Return papers in a Zotero collection with metadata and license classification.

    Args:
        collection_id: Zotero collection key (e.g. "ABC123").
        library_id: Override the configured library_id.
        include_abstract: Include abstractNote in each item (default True).
        limit: Page size, max 500 (default 200).
        cursor: Opaque pagination token from a previous call's ``next_cursor``.

    Returns:
        {"collection_id", "items": [...], "total": int, "next_cursor": str | None}
        Each item: {"zotero_key", "doi", "title", "authors", "year", "abstract",
                    "item_type", "tags", "license": {...}, "has_attachments"}
    """
    import asyncio
    import httpx
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.integrations.zotero_license import LicenseClassifier

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key):
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "Zotero not enabled or api_key missing"}

    eff_library_id = library_id or cfg.library_id
    if not eff_library_id:
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "library_id required"}

    base_url = getattr(cfg, "base_url", "") or None
    client = ZoteroClient(
        api_key=cfg.api_key,
        library_id=eff_library_id,
        library_type=cfg.library_type,
        base_url=base_url,
    )

    try:
        all_items = await client.list_items_in_collection(collection_id, include_subcollections=True)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            return {"error": "ZOTERO_AUTH_FAILED"}
        if status == 429:
            ra = exc.response.headers.get("retry-after") or "60"
            return {"error": "ZOTERO_RATE_LIMITED", "retry_after_s": float(ra)}
        if status == 404:
            return {"error": "COLLECTION_NOT_FOUND", "message": f"Collection {collection_id} not found"}
        return {"error": "ZOTERO_ERROR", "message": str(exc)}

    # Cursor pagination over the already-fetched list (Zotero fetch is paginated internally)
    total = len(all_items)
    limit = max(1, min(limit, 500))
    start = _decode_cursor(cursor) if cursor else 0
    if start < 0 or start > total:
        return {"error": "INVALID_CURSOR", "message": "Cursor is stale or invalid"}
    page = all_items[start: start + limit]
    next_start = start + len(page)
    next_cursor = _encode_cursor(next_start) if next_start < total else None

    # License classification — concurrent, one per item with a shared http client
    clf = LicenseClassifier()
    async with httpx.AsyncClient() as http:
        async def _classify_item(it: dict) -> dict:
            data = it.get("data") or {}
            doi = data.get("DOI") or None
            creators = data.get("creators") or []
            authors = [
                ((cr.get("firstName") or "") + " " + (cr.get("lastName") or cr.get("name") or "")).strip()
                for cr in creators
            ]
            year_str = str(data.get("date") or "")[:4]
            year = int(year_str) if year_str.isdigit() else None
            tags = [(t.get("tag") or "") for t in (data.get("tags") or [])]

            if doi:
                lic = await clf.classify(doi, zotero_item=it, http_client=http)
            else:
                lic = clf.classify_zotero_tags(it) or clf.heuristic(is_oa=False)

            return {
                "zotero_key": it.get("key"),
                "doi": doi,
                "title": data.get("title") or "",
                "authors": [a for a in authors if a],
                "year": year,
                "abstract": data.get("abstractNote") if include_abstract else None,
                "item_type": data.get("itemType") or "journalArticle",
                "tags": tags,
                "license": {
                    "spdx": lic.spdx,
                    "classification": lic.classification,
                    "policy": lic.policy,
                    "source": lic.source,
                },
                "has_attachments": False,  # lightweight — caller uses zotero_get_paper_resources for files
            }

        items = await asyncio.gather(*(_classify_item(it) for it in page))

    return {
        "collection_id": collection_id,
        "items": list(items),
        "total": total,
        "next_cursor": next_cursor,
    }
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "get_collection_items" -v 2>&1 | tail -15
```

Expected: All 3 collection_items tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_zotero_mcp_new_tools.py
git commit -m "$(cat <<'EOF'
feat(mcp): add zotero_get_collection_items with cursor pagination + license enrichment

Returns items with per-item LicenseInfo (classification + policy);
concurrently classifies all items on a page via a shared httpx session.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `zotero_get_paper_resources` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Modify: `tests/unit/test_zotero_mcp_new_tools.py`

### Background

Looks up a Zotero item by DOI (via `GET /items?q={doi}&qmode=everything`) or directly by `zotero_key`. Calls `get_item_attachments()` then `ResourceLocator.build()`. Raises `AMBIGUOUS_DOI` if the DOI search returns more than one item.

- [ ] **Step 5.1: Add tests for `zotero_get_paper_resources`**

Append to `tests/unit/test_zotero_mcp_new_tools.py`:

```python
# --- zotero_get_paper_resources ---

@pytest.mark.asyncio
async def test_get_paper_resources_paper_not_found(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _no_items(self, path, params=None):
        if "/items" in path or (params and "q" in params):
            return []
        raise RuntimeError("unexpected call")

    async def _search_items(self, doi_query):
        return []  # empty = not found

    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    # Patch _search_by_doi on client
    async def _paginated_empty(self, path, params=None):
        return []

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _paginated_empty)
    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.9999/missing")
    assert out["error"] == "PAPER_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_paper_resources_ambiguous_doi(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _two_items(self, path, params=None):
        return [
            {"key": "K1", "data": {"DOI": "10.1234/x", "title": "T1"}},
            {"key": "K2", "data": {"DOI": "10.1234/x", "title": "T2"}},
        ]

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _two_items)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.1234/x")
    assert out["error"] == "AMBIGUOUS_DOI"


@pytest.mark.asyncio
async def test_get_paper_resources_returns_resources(monkeypatch, tmp_path):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod
    from perspicacite.integrations.zotero_license import LicenseInfo

    async def _one_item(self, path, params=None):
        return [{"key": "K1", "data": {"DOI": "10.1234/y", "title": "T"}}]

    async def _no_attachments(self, item_key):
        return []

    async def _fake_classify(self, doi, **kw):
        return LicenseInfo("CC-BY-4.0", "permissive", "verbatim", "crossref")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _one_item)
    monkeypatch.setattr(zotero_mod.ZoteroClient, "get_item_attachments", _no_attachments)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)

    state = _fake_state()
    monkeypatch.setattr(mcp_server, "mcp_state", state)

    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.1234/y")
    assert out.get("doi") == "10.1234/y"
    assert "resources" in out
    assert out["license"]["classification"] == "permissive"
    pdf = next(r for r in out["resources"] if r["role"] == "fulltext_pdf")
    # doi_resolver always present
    doi_access = [a for a in pdf["access"] if a.get("via") == "doi_resolver"]
    assert len(doi_access) == 1
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "get_paper_resources" -x -q 2>&1 | head -10
```

Expected: `AttributeError: ... 'zotero_get_paper_resources'`

- [ ] **Step 5.3: Add `zotero_get_paper_resources` to `server.py`**

Append after `zotero_get_collection_items`:

```python
# =============================================================================
# Tool 15: zotero_get_paper_resources
# =============================================================================


@mcp.tool()
async def zotero_get_paper_resources(
    doi: str | None = None,
    zotero_key: str | None = None,
    library_id: str | None = None,
) -> dict:
    """Return file access options for a paper (local path first, then remote URLs).

    Exactly one of ``doi`` or ``zotero_key`` must be provided.

    Args:
        doi: The paper's DOI.
        zotero_key: Zotero item key (use when DOI is ambiguous).
        library_id: Override the configured library_id.

    Returns:
        {"doi", "zotero_key", "license": {...}, "resources": [...], "notes": []}
        Each resource: {"role", "filename", "access": [{"type", "path"|"url", "via"?}]}
    """
    import httpx
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.integrations.zotero_license import LicenseClassifier
    from perspicacite.integrations.zotero_resources import ResourceLocator

    if not doi and not zotero_key:
        return {"error": "INVALID_ARGUMENTS", "message": "Provide doi or zotero_key"}

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key):
        return {"error": "ZOTERO_NOT_CONFIGURED"}

    eff_library_id = library_id or cfg.library_id
    if not eff_library_id:
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "library_id required"}

    base_url = getattr(cfg, "base_url", "") or None
    client = ZoteroClient(
        api_key=cfg.api_key,
        library_id=eff_library_id,
        library_type=cfg.library_type,
        base_url=base_url,
    )

    try:
        # Resolve to a single Zotero item
        if zotero_key:
            items = await client._paginated(f"/items/{zotero_key}")
            zotero_item = items[0] if items else None
            if zotero_item is None:
                return {"error": "PAPER_NOT_FOUND", "message": f"Key {zotero_key} not found"}
        else:
            items = await client._paginated("/items", params={"q": doi, "qmode": "everything"})
            # Filter to items whose DOI matches exactly
            matched = [
                it for it in items
                if (it.get("data") or {}).get("DOI", "").lower().strip() == doi.lower().strip()
            ]
            if not matched:
                return {"error": "PAPER_NOT_FOUND", "message": f"DOI {doi} not in library"}
            if len(matched) > 1:
                return {
                    "error": "AMBIGUOUS_DOI",
                    "message": f"DOI {doi} matches {len(matched)} items; pass zotero_key",
                    "keys": [it.get("key") for it in matched],
                }
            zotero_item = matched[0]

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            return {"error": "ZOTERO_AUTH_FAILED"}
        if status == 429:
            ra = exc.response.headers.get("retry-after") or "60"
            return {"error": "ZOTERO_RATE_LIMITED", "retry_after_s": float(ra)}
        return {"error": "ZOTERO_ERROR", "message": str(exc)}

    item_doi = (zotero_item.get("data") or {}).get("DOI") or doi
    item_key = zotero_item.get("key") or zotero_key

    # Attachments + license (concurrent)
    import asyncio
    clf = LicenseClassifier()
    async with httpx.AsyncClient() as http:
        attachments, lic = await asyncio.gather(
            client.get_item_attachments(item_key),
            clf.classify(item_doi or "", zotero_item=zotero_item, http_client=http),
        )

    # Notes (plain text)
    try:
        notes = await client.get_item_notes(item_key)
        note_texts = [
            (n.get("data") or {}).get("note") or "" for n in notes
        ]
    except Exception:
        note_texts = []

    rl = ResourceLocator(mcp_state.config)
    resources = rl.build(doi=item_doi or "", zotero_item=zotero_item, attachments=attachments)

    return {
        "doi": item_doi,
        "zotero_key": item_key,
        "license": {
            "spdx": lic.spdx,
            "classification": lic.classification,
            "policy": lic.policy,
            "source": lic.source,
        },
        "resources": resources,
        "notes": [n for n in note_texts if n],
    }
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "get_paper_resources" -v 2>&1 | tail -15
```

Expected: All 3 paper_resources tests pass.

- [ ] **Step 5.5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_zotero_mcp_new_tools.py
git commit -m "$(cat <<'EOF'
feat(mcp): add zotero_get_paper_resources — ordered access list with license info

Resolves paper by DOI or Zotero key; returns local-first file access
list from ResourceLocator. Handles PAPER_NOT_FOUND, AMBIGUOUS_DOI.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `zotero_ingest_collection_to_kb` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Modify: `tests/unit/test_zotero_mcp_new_tools.py`

### Background

Builds a single-entry plan via `zotero_ingest.plan_kbs_from_zotero()` filtered to `collection_id`, then calls `build_kbs_from_zotero()`. If `mcp_state.job_registry` is set (running under the full web server), creates a real background task and returns `job_id` + `poll_url`. Otherwise runs inline (MCP-only context) and returns the finished result directly.

- [ ] **Step 6.1: Add tests for `zotero_ingest_collection_to_kb`**

Append to `tests/unit/test_zotero_mcp_new_tools.py`:

```python
# --- zotero_ingest_collection_to_kb ---

@pytest.mark.asyncio
async def test_ingest_collection_not_configured(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(enabled=False)))
    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(collection_id="AAA")
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_ingest_collection_inline_mode(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_ingest as zi

    async def _fake_plan(client, *, top_level_collection_keys=None, **kw):
        return [
            zi.ZoteroKBPlanEntry(
                kb_name="metabolomics",
                source_collection_key="AAA",
                source_collection_name="Metabolomics",
                item_count=5,
                with_doi_count=5,
                with_pdf_count=0,
                with_notes_count=0,
            )
        ]

    async def _fake_build(client, *, plan, app_state, registry, job_id):
        await registry.finish(job_id, {"per_kb": [{"kb": "metabolomics", "papers": 5}]})

    async def _fake_lib_name(self):
        return "TestLib"

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _fake_plan)
    monkeypatch.setattr(zi, "build_kbs_from_zotero", _fake_build)
    monkeypatch.setattr(zotero_mod.ZoteroClient, "get_library_name", _fake_lib_name)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(
        collection_id="AAA", kb_name="metabolomics"
    )
    # Inline mode returns the result directly (no job_id)
    assert "per_kb" in out or "job_id" in out


@pytest.mark.asyncio
async def test_ingest_collection_not_found(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_ingest as zi

    async def _bad_plan(client, **kw):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
        )

    async def _fake_lib_name(self):
        return "TestLib"

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _bad_plan)
    monkeypatch.setattr(zotero_mod.ZoteroClient, "get_library_name", _fake_lib_name)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(collection_id="NOPE")
    assert out["error"] == "COLLECTION_NOT_FOUND"
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "ingest_collection" -x -q 2>&1 | head -10
```

Expected: `AttributeError: ... 'zotero_ingest_collection_to_kb'`

- [ ] **Step 6.3: Add `zotero_ingest_collection_to_kb` to `server.py`**

Append after `zotero_get_paper_resources`:

```python
# =============================================================================
# Tool 16: zotero_ingest_collection_to_kb
# =============================================================================

# Strong references to background tasks (prevent GC before completion)
_zotero_ingest_tasks: set = set()


@mcp.tool()
async def zotero_ingest_collection_to_kb(
    collection_id: str,
    kb_name: str | None = None,
    library_id: str | None = None,
    force_reingest: bool = False,
) -> dict:
    """Ingest a Zotero collection into a Perspicacité KB.

    If the server has a job registry (running under the full web server),
    the ingest runs as a background task and the call returns immediately
    with a ``job_id`` and ``poll_url``. Otherwise the ingest runs inline
    and the finished result is returned directly.

    Args:
        collection_id: Zotero collection key (e.g. "ABC123").
        kb_name: KB name override; defaults to a sanitized version of the
            collection name.
        library_id: Override the configured library_id.
        force_reingest: Re-embed papers already in the KB (default False).

    Returns (async mode):
        {"job_id", "kb_name", "collection_id", "item_count", "status": "running", "poll_url"}
    Returns (inline mode):
        {"per_kb": [...]} from build_kbs_from_zotero
    """
    import asyncio
    import httpx
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.integrations import zotero_ingest

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key):
        return {"error": "ZOTERO_NOT_CONFIGURED"}

    eff_library_id = library_id or cfg.library_id
    if not eff_library_id:
        return {"error": "ZOTERO_NOT_CONFIGURED", "message": "library_id required"}

    base_url = getattr(cfg, "base_url", "") or None
    client = ZoteroClient(
        api_key=cfg.api_key,
        library_id=eff_library_id,
        library_type=cfg.library_type,
        collection_key=cfg.collection_key,
        base_url=base_url,
    )

    try:
        library_name = await client.get_library_name() or "Library"
        # Build plan filtered to this collection only
        plan = await zotero_ingest.plan_kbs_from_zotero(
            client,
            top_level_collection_keys=[collection_id],
            include_unfiled=False,
            library_label=library_name,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            return {"error": "ZOTERO_AUTH_FAILED"}
        if status == 429:
            ra = exc.response.headers.get("retry-after") or "60"
            return {"error": "ZOTERO_RATE_LIMITED", "retry_after_s": float(ra)}
        if status == 404:
            return {"error": "COLLECTION_NOT_FOUND", "message": f"Collection {collection_id} not found"}
        return {"error": "ZOTERO_ERROR", "message": str(exc)}

    if not plan:
        return {"error": "COLLECTION_NOT_FOUND", "message": f"Collection {collection_id} produced no plan entries"}

    entry = plan[0]
    effective_kb = kb_name or entry.kb_name

    # Override kb_name if caller supplied one
    if kb_name:
        from perspicacite.integrations.zotero_ingest import ZoteroKBPlanEntry
        entry = ZoteroKBPlanEntry(
            kb_name=kb_name,
            source_collection_key=entry.source_collection_key,
            source_collection_name=entry.source_collection_name,
            item_count=entry.item_count,
            with_doi_count=entry.with_doi_count,
            with_pdf_count=entry.with_pdf_count,
            with_notes_count=entry.with_notes_count,
        )

    registry = getattr(mcp_state, "job_registry", None)

    if registry is not None:
        # Async mode: create real job, fire background task
        job_id = await registry.create("zotero_collection_ingest", total=entry.item_count)
        task = asyncio.create_task(
            zotero_ingest.build_kbs_from_zotero(
                client,
                plan=[entry],
                app_state=mcp_state,
                registry=registry,
                job_id=job_id,
            )
        )
        _zotero_ingest_tasks.add(task)
        task.add_done_callback(_zotero_ingest_tasks.discard)
        base = getattr(mcp_state.config, "server", None)
        port = getattr(base, "port", 5468) if base else 5468
        return {
            "job_id": job_id,
            "kb_name": effective_kb,
            "collection_id": collection_id,
            "item_count": entry.item_count,
            "status": "running",
            "poll_url": f"http://localhost:{port}/api/jobs/{job_id}/events",
        }

    # Inline mode (MCP-only context, no registry)
    class _InlineReg:
        def __init__(self):
            self.result = None
            self.err = None
        async def publish(self, jid, ev):
            return
        async def finish(self, jid, res):
            self.result = res
        async def fail(self, jid, err):
            self.err = err

    reg = _InlineReg()
    try:
        await zotero_ingest.build_kbs_from_zotero(
            client,
            plan=[entry],
            app_state=mcp_state,
            registry=reg,
            job_id="mcp-inline",
        )
    except Exception as exc:
        return {"error": str(exc)}
    if reg.err:
        return {"error": reg.err}
    return reg.result or {"per_kb": []}
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -k "ingest_collection" -v 2>&1 | tail -15
```

Expected: All 3 ingest_collection tests pass.

- [ ] **Step 6.5: Run full new-tools test suite**

```bash
PYTHONPATH=src pytest tests/unit/test_zotero_mcp_new_tools.py -v 2>&1 | tail -25
```

Expected: All tests pass (12+ total).

- [ ] **Step 6.6: Run full unit suite to check for regressions**

```bash
PYTHONPATH=src pytest tests/unit -q --tb=line 2>&1 | tail -10
```

Expected: 1316+ passed, 0 failed.

- [ ] **Step 6.7: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_zotero_mcp_new_tools.py
git commit -m "$(cat <<'EOF'
feat(mcp): add zotero_ingest_collection_to_kb — async job or inline ingest

Uses real job registry when available (returns job_id + poll_url);
falls back to inline execution in MCP-only context. Wraps existing
build_kbs_from_zotero with single-collection plan filter.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update `get_info` tool count + docs

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (update docstring tool count)
- Modify: `docs/reference/mcp-tools.md`

The existing `get_info` test asserts `tool_count >= 12`. After adding 4 tools it should assert `>= 16`. The docs need entries for all 4 new tools.

- [ ] **Step 7.1: Verify `get_info` still passes (count only grows)**

```bash
PYTHONPATH=src pytest tests/unit/test_mcp_zotero_ingest_tool.py::test_get_info_lists_twelve_tools -v 2>&1
```

Expected: Pass (count is now 16 but assertion is `>= 12`).

- [ ] **Step 7.2: Update the server module docstring in `server.py`**

Find the module docstring (lines 1-20) and update the tools list:

```python
"""MCP server implementation for Perspicacité v2.
...
- push_to_zotero: Push DOIs to the configured Zotero library
- build_kbs_from_zotero: Build one KB per Zotero top-level collection
- build_kb_from_search: Search SciLEx, filter, fetch PDFs, ingest into a KB
- zotero_list_collections: List all Zotero collections with sub-collection tree
- zotero_get_collection_items: Get papers in a collection with license classification
- zotero_get_paper_resources: Get ordered file access options for a paper
- zotero_ingest_collection_to_kb: Ingest a Zotero collection into a KB
"""
```

- [ ] **Step 7.3: Add the 4 new tools to `docs/reference/mcp-tools.md`**

Open `docs/reference/mcp-tools.md` and add a new section after `## Export and integration`:

```markdown
---

## Zotero read path (for ASB integration)

### `zotero_list_collections`

List all Zotero library collections as a nested tree.

**Parameters:**
- `library_id` (str, optional) — override configured library_id
- `include_subcollections` (bool, default true) — return nested tree

**Returns:** `{collections: [{id, name, parent_id, item_count, subcollections}], library_id, library_type}`

**Errors:** `ZOTERO_NOT_CONFIGURED`, `ZOTERO_AUTH_FAILED`, `ZOTERO_RATE_LIMITED`, `LIBRARY_NOT_FOUND`

### `zotero_get_collection_items`

Return papers in a collection with metadata and per-paper license classification.

**Parameters:**
- `collection_id` (str) — Zotero collection key
- `library_id` (str, optional)
- `include_abstract` (bool, default true)
- `limit` (int, default 200, max 500)
- `cursor` (str, optional) — pagination token from previous call's `next_cursor`

**Returns:** `{collection_id, items: [{zotero_key, doi, title, authors, year, abstract, item_type, tags, license: {spdx, classification, policy, source}, has_attachments}], total, next_cursor}`

**License policy:** `classification=permissive` → `policy=verbatim` (text may be copied); `classification=closed|unknown` → `policy=reflavor` (must paraphrase).

**Errors:** `ZOTERO_NOT_CONFIGURED`, `ZOTERO_AUTH_FAILED`, `ZOTERO_RATE_LIMITED`, `COLLECTION_NOT_FOUND`, `INVALID_CURSOR`

### `zotero_get_paper_resources`

Return ordered file access options for a single paper. Local paths come first (from Perspicacité's PDF cache and capsule supplementary-file storage); remote URLs follow in priority order.

**Parameters:**
- `doi` (str, optional) — the paper's DOI
- `zotero_key` (str, optional) — Zotero item key; use when DOI is ambiguous
- `library_id` (str, optional)

Exactly one of `doi` or `zotero_key` must be provided.

**Returns:** `{doi, zotero_key, license: {...}, resources: [{role, filename, access: [{type, path|url, via?}]}], notes: [str]}`

`role` values: `fulltext_pdf`, `supplementary`, `note`. Access `type`: `local` (on-disk path) or `remote` (URL + `via` label: `publisher`, `doi_resolver`).

**Errors:** `ZOTERO_NOT_CONFIGURED`, `ZOTERO_AUTH_FAILED`, `ZOTERO_RATE_LIMITED`, `PAPER_NOT_FOUND`, `AMBIGUOUS_DOI`

### `zotero_ingest_collection_to_kb`

Ingest a Zotero collection into a Perspicacité KB. Returns immediately with a `job_id` when running under the full web server (poll `poll_url` for completion); runs inline in MCP-only mode.

**Parameters:**
- `collection_id` (str) — Zotero collection key
- `kb_name` (str, optional) — KB name; defaults to sanitized collection name
- `library_id` (str, optional)
- `force_reingest` (bool, default false) — re-embed already-indexed papers

**Returns (async):** `{job_id, kb_name, collection_id, item_count, status: "running", poll_url}`

**Returns (inline):** `{per_kb: [...]}`

After the job completes, use `search_knowledge_base` or `generate_report` with the returned `kb_name`.

**Errors:** `ZOTERO_NOT_CONFIGURED`, `ZOTERO_AUTH_FAILED`, `ZOTERO_RATE_LIMITED`, `COLLECTION_NOT_FOUND`, `KB_NAME_CONFLICT`
```

- [ ] **Step 7.4: Commit**

```bash
git add src/perspicacite/mcp/server.py docs/reference/mcp-tools.md
git commit -m "$(cat <<'EOF'
docs(mcp): add 4 new Zotero tools to mcp-tools.md reference + server docstring

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist

**Spec coverage against `docs/superpowers/specs/2026-05-16-zotero-mcp-tools-design.md`:**

| Spec requirement | Task |
|---|---|
| `LicenseClassifier` with Crossref→OpenAlex→tags→heuristic | Task 1 |
| 7-day TTL cache on license results | Task 1 (`_store`, `get_cached`) |
| `ResourceLocator` local-first + remote fallback | Task 2 |
| `zotero_list_collections` with 1hr cache + nested tree | Task 3 |
| `zotero_get_collection_items` with cursor pagination + license | Task 4 |
| `zotero_get_paper_resources` with AMBIGUOUS_DOI | Task 5 |
| `zotero_ingest_collection_to_kb` with async/inline modes | Task 6 |
| All 9 error codes | Tasks 3–6 |
| `docs/reference/mcp-tools.md` update | Task 7 |
| Unit tests for license classifier (12+ assertions) | Task 1 (18 tests) |
| Unit tests for resource locator | Task 2 (5 tests) |
| Unit tests for MCP tools (all error codes) | Tasks 3–6 (12+ tests) |

**Placeholder scan:** No TBD, TODO, or incomplete stubs in any task. ✓

**Type consistency:**
- `LicenseInfo.classification` / `.policy` / `.source` / `.spdx` used consistently Tasks 1→4→5
- `ResourceLocator.build(doi, zotero_item, attachments)` signature consistent Task 2→5
- `_encode_cursor` / `_decode_cursor` defined in Task 4 only, used only in Task 4 ✓
