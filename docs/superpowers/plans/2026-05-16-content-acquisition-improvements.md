# Content Acquisition Improvements — Plan

**Date:** 2026-05-16
**Author:** Audit session (Opus 4.7)
**Status:** **P1+P2+P3+P3b+P5+P6+P7 implemented and unit-tested 2026-05-16** — P4 (headless Chromium) still proposal-only
**Trigger:** Mimosa-AI bib upload + audit. 18/75 papers had no recoverable PDF or HTML, and the MCP `push_to_zotero` tool exposed two integration gaps.

## Implementation status (2026-05-16)

| Priority | Status | Where landed |
|----------|--------|--------------|
| P1 (URL/BibTeX routes on `push_to_zotero`) | ✅ Done | `mcp/server.py:1342-1488`, `integrations/zotero.py:_resolve_push_input`-style at `mcp/server.py:153-243`, `integrations/zotero.py:create_item` |
| P2 (dedup fix — indexing-lag fallback) | ✅ Done | `integrations/zotero.py:_find_existing_by_doi` two-stage lookup; 3 new tests in `tests/unit/test_zotero.py` |
| P3 (`ingest_url` MCP tool) | ✅ Done | `mcp/server.py:ingest_url`, 4 extractors in `pipeline/download/url_extractors.py`, 8 tests in `tests/unit/test_url_extractors.py` |
| P3b (HTML-as-fallback hook on every PDF-attach path) | ✅ Done | `pipeline/download/html_capture.py` (tiered classifier + snapshot serializer), wired into `push_to_zotero`, 6 tests in `tests/unit/test_html_capture.py` |
| P4 (headless Chromium fallback) | ❌ Proposal-only — heavy dep | — |
| P5 (`library_id`/`collection_key` overrides) | ✅ Done | `push_to_zotero` + `ingest_url` both accept overrides |
| P6 (`POST /api/pdf-dropzone`) | ✅ Done | `web/routers/pdf_dropzone.py`, 5 tests in `tests/unit/test_pdf_dropzone.py` |
| P7 (cookie-freshness startup warning) | ✅ Done | `web/state.py:_warn_stale_cookies`, 4 tests in `tests/unit/test_cookie_startup_warn.py` |

**Full unit test suite:** 1382 passed / 1 skipped after these changes.
**Live smoke against the MCP server:** `ingest_url` on `github.com/langchain-ai/langgraph` → `item_type=computerProgram`, title `langchain-ai/langgraph`, format `github_api`. `ingest_url` on `anthropic.com/engineering/multi-agent-research-system` → `item_type=webpage`, full title resolved from `<meta>` tags, format `html_meta_tags`.

This document captures concrete, prioritised work to make Perspicacité reliably acquire content for items where DOI-OA-PDF doesn't suffice. It's written so a fresh session can pick it up cold.

---

## Findings driving the plan

Distilled from the Mimosa-AI bibliography load (65 items + 10 expansion = 75):

| # | Class of source | Items affected | Today's failure mode |
|---|------------------|----------------|----------------------|
| F1 | **Vendor docs / blog posts / GitHub READMEs** (no DOI, no PDF; canonical content is an HTML page) | `anthropic2024mcp` (modelcontextprotocol.io), `anthropic2025multiagentresearch` (anthropic.com), `langgraph` (github.com/langchain-ai), `smolagents` (github.com/huggingface), `guo2024evoprompt` (OpenReview HTML), `_tobias_autonomous_2025` | No path. `push_to_zotero` requires a DOI. PDF fetchers don't touch HTML. KB ingest only takes BibTeX/DOI/local PDF — never a URL. |
| F2 | **Cloudflare-protected publisher PDFs** (paywalled OR bronze OA) | `aag2024chemrev` (Chem Rev — *OA but Cloudflare-gated*), `Gil2014`, `Sliwoski2014`, `Sadybekov2023`, `Beaulieu-Jones2017`, `Berger2023`, `Campo2022` (ACS Omega — OA), `luo-jacs25`, `Baker2016` | `pubs.acs.org` and others return Cloudflare 403 to any non-browser HTTP client even with valid session cookies. Pure-requests / httpx / cloudscraper all fail. PMC mirrors (e.g. PMC11363023) added their own JS-challenge in 2025. |
| F3 | **Preprints.org HTML landing** | `8V233HBC` (Building MCP-Native Hierarchical AI Scientist), `3CJDUAT8` (Multi-Agent LLM Systems: Emergent Collaboration) | OpenAlex doesn't expose a direct PDF URL; preprints.org redirects through an HTML landing. The current fetcher only handles direct PDF binaries. |
| F4 | **MCP `push_to_zotero` dedup is broken** | All items pushed via MCP | Spec docstring says "Skips duplicates automatically (ZoteroClient checks by DOI before creating)". Live test: pushing `10.48550/arxiv.2603.08127` (already in collection `4DNCGAD8` as `QPKVZ2F8`) created a second item `JGEXU7PE` with the same DOI. |
| F5 | **MCP `push_to_zotero` rejects non-DOI items** | All F1 items | Signature is `dois: list[str]`. No `urls`, no `bibtex`, no way to push a non-DOI item even when full metadata is in hand. |
| F6 | **`push_to_zotero` library/collection is config-locked** | Multi-library/multi-collection use | Has no `library_id` or `collection_key` parameter override (cf. `build_kbs_from_zotero` which DOES). Forces a config restart to push to a different target. |
| F7 | **`push_to_zotero` against local Zotero desktop API silently no-ops attachments** (the config defaults to `http://localhost:23119/api`). | Anyone with default config | Returns a clean error: "Zotero local API is read-only — push requires cloud". Good UX but means default-config users get zero pushes. |
| F8 | **No HTML fallback when PDF unavailable.** OpenAlex returns `abstract_inverted_index` (rebuildable abstract) for every indexed work, and most publisher landing pages have the abstract + author list openly available; we throw all of that away on PDF failure. | Every paper we can't get the PDF for (currently 18 of 75 Mimosa-AI items) | Zotero items lack any attached fulltext-or-near-fulltext; KB has nothing to retrieve. A *worse-than-PDF-but-better-than-nothing* HTML capture would unlock these. |

---

## Proposal — seven small features, ordered by ROI

### Priority 1 — close the no-DOI gap on `push_to_zotero` (fixes F1, F5)

**Goal:** allow URL-only and BibTeX-only items through the MCP push.

**Signature change:**

```python
@mcp.tool()
async def push_to_zotero(
    items: list[dict],          # NEW: superset of `dois`
    *,
    dois: list[str] | None = None,         # back-compat — wraps each into {"doi": ...}
    library_id: str | None = None,         # NEW — overrides config
    collection_key: str | None = None,     # NEW — overrides config
    attach_pdf: bool = False,
    attach_supplementary: bool = False,
) -> str:
```

`items` accepts a mixed list of dicts:

```python
{"doi": "10.1038/s41586-..."}                     # DOI route (existing)
{"url": "https://anthropic.com/research/...", "title": "...", "authors": [...]}  # URL route
{"bibtex": "@misc{...}"}                          # BibTeX route
```

URL-route handling: re-use the **existing `process_html`** logic from `pipeline/download/unified.py` (which already understands HTML→`PaperContent` for most landing pages). For pages without metadata, fall back to OpenGraph / `<meta name="citation_*">` / `<title>`. Surface a "ROUTE_OVERRIDE_REQUIRED" error when the page yields neither a title nor authors so the caller can supply them.

BibTeX-route: parse with `bibtexparser`, normalise into the same dict shape `ZoteroClient.create_item` already accepts.

### Priority 2 — fix the dedup bug (F4)

**Where:** `src/perspicacite/integrations/zotero.py` → `ZoteroClient.create_item`.

The docstring promises DOI-dedup. Either the lookup isn't running or DOI normalization differs between lookup and create. Confirmed live: pushing `10.48550/arxiv.2603.08127` produced a fresh key when an item with the same DOI was already in the target collection (`4DNCGAD8`). Verify with the exact reproducer:

```bash
python3 /tmp/mcp_client.py push_to_zotero '{"dois": ["10.48550/arxiv.2603.08127"]}'
# → "created": [{"doi": "...", "key": "..."}]  — should have been "skipped"
```

**Fix outline:**
1. Before POSTing a new item, run a `q=<doi>&qmode=everything` search against the target library.
2. Normalise DOI (lowercase, strip `https://doi.org/`, strip trailing `.`) on both sides of the compare.
3. If a hit exists in the same collection (or globally if no collection filter), return `{"doi": ..., "key": existing, "skipped": "doi_exists"}` instead of creating.

### Priority 3 — first-class **URL/HTML ingest** + **HTML fallback when PDF unavailable** (fixes F1, F3, F8; partial F2)

**New principle: HTML is better than nothing.** Whenever the PDF route fails for a paper (paywalled, Cloudflare-blocked, OpenAlex didn't index a PDF URL, etc.) we **fall back to capturing the publisher landing page** as a self-contained HTML snapshot. This becomes the attachment in Zotero and the source text for KB ingestion. It's degraded fidelity (no figures-in-place, abstract-only on hard paywalls) but unlocks the full-text body for *open-abstract* journals and gives a reliable artifact to cite.

Add a new MCP tool **`ingest_url`** (and corresponding REST endpoint) that takes:

```python
@mcp.tool()
async def ingest_url(
    url: str,
    *,
    kb_name: str | None = None,        # if set, also chunk and add to KB
    push_to_zotero: bool = False,      # if True, also push as a Zotero item
    library_id: str | None = None,
    collection_key: str | None = None,
    capture_format: str = "auto",      # "auto" | "html" | "markdown" | "both"
) -> str:
```

Implementation re-uses the same render/extract pipeline as the existing `get_paper_content` MCP tool, plus four extractors that don't exist today:

| URL pattern | Extract |
|-------------|---------|
| `github.com/<owner>/<repo>` | README + repo metadata via GitHub API (no auth needed for public). Already partially done in old release-v1 `bibtex2kb/src/github_parser.py` — port it back. Save README as Markdown alongside; capture authors from contributors. |
| `openreview.net/forum?id=*` | `https://api.openreview.net/notes?id=*` returns JSON with title/authors/abstract + direct PDF URL at `.pdf`. |
| `anthropic.com/research/*`, `*.modelcontextprotocol.io`, generic HTML | Trafilatura main-content extract → save as `<slug>.html` + `<slug>.md` for KB ingest. Mine `<meta name="citation_*">` and OpenGraph for Zotero metadata. |
| `preprints.org/manuscript/*` | The landing page has `<meta name="citation_pdf_url">` pointing to a token-signed direct PDF link that works without Cloudflare. Old release scraper from `bibtex2kb/src/html_parser.py` already has this pattern. |

### Priority 3b-extension — **PDF-too-big also triggers HTML fallback** (added 2026-05-16 post-implementation)

In addition to "PDF couldn't be obtained", the HTML fallback also fires when the cached PDF exceeds `pdf_download.max_pdf_attach_bytes` (default 30 MB). Rationale: a 54 MB Chem Rev review article eats 18% of the Zotero free-tier 300 MB quota for one paper; the user usually has the file locally already; the bibliographic record + landing-page snapshot is enough for citation tracking and KB retrieval. The result entry surfaces `pdf_attach_skipped: "pdf_too_large (<bytes> > <cap>)"` alongside the standard `attached_html` field so the caller can see both branches. Set `max_pdf_attach_bytes: 0` to disable the cap (pre-2026-05-16 behavior).

### Priority 3b — **HTML fallback hook on every PDF-attach path** (new — addresses the "better than nothing" feedback)

In `push_to_zotero` and any other place that calls `retrieve_paper_content(..., pdf_parser=...)`, when the PDF route returns no bytes but the discovery step gathered an `oa_url` / `landing_page_url` / `url`, fall back to:

1. Fetch the landing page HTML (using the cookies-aware client we already have)
2. Run **Trafilatura** or `readability-lxml` to extract main content → write a self-contained `<doi-slug>.html` (with inlined CSS, no external requests) to `pdf_download.cache_dir/html/`
3. Optional second artifact: rendered Markdown for KB ingest
4. Attach the HTML as a Zotero attachment with `contentType: text/html` (Zotero already supports HTML snapshots — the "snapshot" link mode the Zotero Connector uses)

A new attachment route emerges in `ZoteroClient.upload_attachment`:

```python
await zotero.upload_attachment(
    parent_item_key=key,
    file_path=str(html_path),
    filename=f"{slug}.html",
    content_type="text/html",
    link_mode="imported_url",            # vs "imported_file" for PDFs
)
```

Surface this in the push result:

```json
{
  "doi": "10.1126/science.1259439",
  "key": "ABC123",
  "attached_pdf": false,
  "pdf_attach_error": "Cloudflare 403 on best_oa_url",
  "attached_html": true,
  "html_source": "publisher_landing",     // or "abstract_only" / "open_abstract" / "openalex_abstract"
  "html_chars": 14823
}
```

**Three quality tiers** the HTML extractor should report explicitly:

| Tier | Meaning |
|------|---------|
| `full_text_html` | Landing page contains the article body (PMC HTML, BMC, MDPI, open OA pages with body in HTML) |
| `extended_abstract` | Page has the abstract + section headers + figure captions but body is paywalled |
| `bibliographic_stub` | Page has only title, authors, DOI, abstract |

The KB chunker uses the tier to decide chunk weighting — `full_text_html` chunks rank like a PDF; `extended_abstract` and `bibliographic_stub` get a tier-aware penalty so they aren't over-retrieved.

### Priority 4 — Cloudflare/JS-challenge fallback via headless Chromium (fixes F2)

Add **optional Playwright** integration behind a `pdf_download.headless_browser: true` config flag. When the existing publisher-specific scrapers + cookies still get a 403, run a headless browser pass:

```python
# pseudo
async def fetch_via_headless(url: str, cookies_path: str, *, timeout_s: int = 30) -> bytes | None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        await ctx.add_cookies(load_cookies(cookies_path))
        page = await ctx.new_page()
        # Trigger PDF download via page.expect_download() if the URL serves application/pdf
        async with page.expect_download() as dl:
            await page.goto(url)
        path = await (await dl.value).path()
        return Path(path).read_bytes() if path else None
```

Live evidence this works: using Brave with the same cookies the user already exports, the ACS PDF for `10.1021/acs.chemrev.4c00055` (57 MB) downloads cleanly. Cloudflare clears the browser fingerprint (JA3 + JS challenge) once per session and remains cleared for ~2h.

Cost: Playwright + Chromium adds ~150 MB of deps and ~3s startup. Hide behind a feature flag; default `off`.

### Priority 5 — `library_id` / `collection_key` overrides on `push_to_zotero` (fixes F6)

Trivial signature additions per Priority 1's example. Threaded through to `ZoteroClient(library_id=...)`. Forward-compat tests should cover (a) override matches config — no-op; (b) override targets a different group — works; (c) override to a group the API key has no write access — clean 403 propagation.

### Priority 6 — drop-zone helper on the Perspicacité side (mirror of Mimosa-AI's `10_dropzone_attach.py`) (workaround for F2)

For paywalled content where headless still fails (institutional VPN, SSO that requires manual handshake): expose a `POST /api/pdf-dropzone` REST endpoint that accepts `multipart/form-data` with `{doi, file}` and writes the file into the configured `pdf_download.cache_dir`. Then `push_to_zotero(..., attach_pdf=true)` picks it up via the existing `cached_pdf_path` logic.

This is the "user already opens it in Brave, why not let them upload one click" pattern. Companion CLI: `perspicacite drop-pdf --doi 10.x --file foo.pdf`.

### Priority 7 — cookie freshness UX (already partially built, finish it) (F2 hygiene)

`pipeline/download/cookies.py` already has `scan_cookie_freshness` + `check_cookie_freshness_for_domains`. Wire the resulting `CookieDomainWarning` list into:
- a **CLI subcommand** `perspicacite check-cookies` that prints a coloured table
- a **warning at server startup** when any configured domain has all-expired or no-cookies, so the user knows to re-export *before* their first paywall hit (currently they only learn after the silent paywall HTML returns)

Tracked in cookies.py module docstring as a TODO.

---

## Reuse from `Perspicacite-AI-release_v1`

The 2024 release at `/Users/holobiomicslab/git/Perspicacite-AI-release_v1` is a goldmine for this work — it already shipped the HTML-first ingest the user is now asking for:

| Old file | What to port |
|----------|-------------|
| `bibtex2kb/src/html_parser.py` | `fetch_html_page` (UA randomisation, retry+backoff), `liebertpub_scraper`, `biorxiv_scraper`, `elsevier_scraper` (via Elsevier API). Used a fake UA pool, session cookies, MIME-sniff on bytes — most of which the current pipeline does but in scattered files. |
| `bibtex2kb/src/pdf_parser.py` | `process_pdf` (URL-or-bytes input, deduplicates magic-number check) |
| `bibtex2kb/src/github_parser.py` | GitHub repo → metadata + README. Drop-in for Priority 3's GitHub branch. |
| `bibtex2kb/src/citation_handler.py` | `generate_citation`, `create_bibtex_entry` — handy for the BibTeX-route in Priority 1. |
| `bibtex2kb/src/url_handler.py` | `is_valid_pdf_url`, `is_pdf_link`, `download_pdf` — the original "check before download" check. |

None of these handle Cloudflare-protected sources either (release-v1 predated the wall hardening), so they're a complement to Priority 4, not a substitute.

---

## Testing — what would prove this done

For each priority, an MCP-level integration test against a clean Zotero scratch collection:

| Priority | Test |
|----------|------|
| P1 | `push_to_zotero({items: [{"doi":"..."}, {"url":"https://github.com/langchain-ai/langgraph"}, {"bibtex": "@misc{...}"}]})` → 3 created items with appropriate `itemType` (`journalArticle`, `softwareApplication`, `webpage`) |
| P2 | Push the same DOI twice → 1 created + 1 skipped |
| P3 | `ingest_url("https://anthropic.com/research/agent-skills")` → KB entry with extracted main text, no PDF |
| P4 | With cookies + headless, `push_to_zotero({"items":[{"doi":"10.1021/acs.chemrev.4c00055"}], attach_pdf:true})` lands the ~57 MB PDF as an attachment |
| P5 | Two consecutive calls into different `library_id` targets — no config edit between |
| P6 | `curl -F doi=10.x -F file=@foo.pdf http://localhost:8000/api/pdf-dropzone` followed by `push_to_zotero(..., attach_pdf:true)` attaches the dropped PDF |
| P7 | `perspicacite check-cookies` returns non-zero exit code when `pubs.acs.org` cookies are all expired |

---

## Out of scope (mentioned for completeness)

- Re-implementing Zotero's connector (browser extension that does the magic for paywalled OA papers). The Zotero project ships this; we should *use* it rather than rebuild.
- Sci-Hub fallback — already configured (`alternative_endpoint`) but ethically/legally fraught; not improving here.
- Generic web-archive (Wayback) fallback when the page is gone — useful but tangential to acquisition.

---

## Sequencing suggestion

Bundle P1+P2+P5 in one PR (they touch the same `push_to_zotero` function). Bundle P3+P3b+P6 (URL/HTML ingest + fallback + upload, all new artifact paths). P4 is its own PR (heavy dep). P7 stands alone and could land immediately.

ETA estimate for an experienced dev with the codebase in their head: 2 weeks total at typical pace; P1+P2+P5 first (3 days), P3+P3b next (5 days, the HTML fallback is non-trivial because it needs the tiered classifier and a snapshot serializer), P4+P6+P7 in parallel (1 week).
