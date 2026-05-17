# OpenRouter Web-Search CAPTCHA Fallback Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When `GoogleScholarPlaywrightProvider` detects a CAPTCHA, fall back to an OpenRouter `web_search` server-tool call (Exa engine + academic domain allowlist) that returns structured paper results instead of silently returning `[]`.

**Architecture:** A self-contained `_openrouter_fallback.py` helper module is called from the CAPTCHA branch in `google_scholar_playwright.py`. The aggregator and all callers are unaffected — the Scholar provider still returns `list[Paper]` as usual. Papers sourced from the fallback carry `source=PaperSource.OPENROUTER_WEB` for provenance tracing.

**Tech Stack:** `httpx` (already a dependency), OpenRouter Chat Completions API, `openrouter:web_search` server tool (Exa engine), `deepseek/deepseek-v2-fast` as the default model (configurable).

---

## Context

`GoogleScholarPlaywrightProvider` uses headless Chromium to scrape Scholar results. When Scholar serves a CAPTCHA or "unusual traffic" page (detected at line 115 of `google_scholar_playwright.py`), it currently logs a warning and returns `[]`. This silently drops the provider's contribution from the aggregator.

The fix: on CAPTCHA detection, call OpenRouter's web-search server tool with the same query. OpenRouter routes the query through Exa (a semantic search engine), restricts results to academic domains, and returns snippets which the LLM formats as a JSON array of papers. Cost: ~$0.005 per fallback trigger.

---

## Files

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/perspicacite/search/openrouter_fallback.py` | HTTP call to OpenRouter, prompt construction, JSON parsing, `Paper` assembly |
| Modify | `src/perspicacite/search/google_scholar_playwright.py` | Import helper, call it in the CAPTCHA branch |
| Modify | `src/perspicacite/models/papers.py` | Add `PaperSource.OPENROUTER_WEB = "openrouter_web"` |
| Modify | `src/perspicacite/config/schema.py` | Add `openrouter_fallback_*` fields to `GoogleScholarConfig` |
| Modify | `config.example.yml` | Document new keys under `google_scholar:` |
| Create | `tests/unit/test_openrouter_fallback.py` | Unit tests for helper (mocked HTTP) |
| Modify | `tests/unit/test_google_scholar_playwright.py` | Add CAPTCHA-triggers-fallback test |

---

## Component Design

### 1. `openrouter_fallback.py`

```python
async def openrouter_academic_search(
    query: str,
    *,
    api_key: str,
    model: str = "deepseek/deepseek-v2-fast",
    max_results: int = 10,
    allowed_domains: list[str] | None = None,
    timeout: float = 20.0,
) -> list[Paper]:
    """Call OpenRouter web_search server tool; return Paper objects or [] on error."""
```

**Request payload:**
```json
{
  "model": "<model>",
  "tool_choice": "required",
  "tools": [{
    "type": "openrouter:web_search",
    "parameters": {
      "engine": "exa",
      "max_results": 10,
      "allowed_domains": ["arxiv.org", "biorxiv.org", ...]
    }
  }],
  "messages": [{
    "role": "user",
    "content": "Search scientific literature for: {query}\nReturn ONLY a JSON array of up to {n} papers, each: {\"title\": str, \"authors\": [str], \"year\": int|null, \"doi\": str|null, \"abstract\": str, \"url\": str}\nNo prose. No markdown. Just the JSON array."
  }]
}
```

- `tool_choice: "required"` — forces the model to call the search tool; prevents hallucination from training-data memory
- `engine: "exa"` — explicit, because non-native models (DeepSeek) have no native search; `auto` would try native and degrade
- For native-search models (Anthropic, OpenAI, xAI) users should set `engine: "native"` in config

**API key resolution** (in priority order):
1. `google_scholar.openrouter_api_key` in config (if non-empty)
2. `OPENROUTER_API_KEY` environment variable
3. If neither is set: log `openrouter_fallback_no_key`, return `[]`

**Response parsing:**
1. Extract `response["choices"][0]["message"]["content"]`
2. Find first `[...]` JSON array with `re.search(r'\[.*?\]', content, re.DOTALL)`
3. `json.loads()` the match → list of dicts
4. For each dict: build `Paper(id=doi or url-hash, source=PaperSource.OPENROUTER_WEB, ...)`
5. Missing/null fields → `None`; any exception → log `openrouter_fallback_parse_error`, return `[]`

**Error handling:** Any `httpx` error, non-200 response, or parse failure logs a structured warning and returns `[]`. The fallback itself never raises.

### 2. `google_scholar_playwright.py` change

Replace the CAPTCHA branch:

```python
# Before
if "captcha" in html.lower() or "unusual traffic" in html.lower():
    logger.warning("google_scholar_captcha_detected", url=url[:100])
    return []

# After
if "captcha" in html.lower() or "unusual traffic" in html.lower():
    logger.warning("google_scholar_captcha_detected", url=url[:100])
    return _CAPTCHA_FALLBACK_CARDS  # sentinel — handled in search()
```

A module-level sentinel distinguishes "CAPTCHA detected" from "genuinely no results":

```python
# module level
_CAPTCHA_SENTINEL: list[dict[str, str]] = []   # unique identity object
```

The CAPTCHA branch assigns this sentinel:

```python
# in _render_and_extract_cards — CAPTCHA branch
logger.warning("google_scholar_captcha_detected", url=url[:100])
return _CAPTCHA_SENTINEL   # same object, not a new []
```

`search()` checks identity, not equality:

```python
cards = await _render_and_extract_cards(...)
if cards is _CAPTCHA_SENTINEL:
    if scholar_config.openrouter_fallback_enabled:
        return await _run_openrouter_fallback(query, max_results, scholar_config)
    return []
# normal card→Paper loop follows
```

`_run_openrouter_fallback` calls `openrouter_academic_search()` and returns its `list[Paper]` directly, bypassing the card→Paper conversion loop. This is safe because `retry=0` on the provider — no concurrent calls share state.

### 3. `PaperSource.OPENROUTER_WEB`

Added after `DBLP_SPARQL` in the enum:

```python
OPENROUTER_WEB = "openrouter_web"
```

### 4. Config additions (`GoogleScholarConfig`)

```python
openrouter_fallback_enabled: bool = True
openrouter_api_key: str = ""          # also read from OPENROUTER_API_KEY env var
openrouter_fallback_model: str = "deepseek/deepseek-v2-fast"
openrouter_fallback_domains: list[str] = [
    "arxiv.org", "biorxiv.org", "chemrxiv.org",
    "pubmed.ncbi.nlm.nih.gov", "europepmc.org",
    "semanticscholar.org", "crossref.org",
    "nature.com", "sciencedirect.com",
    "springer.com", "wiley.com",
]
```

`openrouter_fallback_enabled: true` by default — when the Scholar provider is active, the fallback is active. Disabled when `google_scholar.enabled: false` (the provider is never registered).

---

## Data Flow

```
GoogleScholarPlaywrightProvider.search(query)
  → _render_and_extract_cards(url, ...)
      → Playwright navigates to Scholar
      → CAPTCHA detected → return sentinel
  → sentinel detected in search()
  → _run_openrouter_fallback(query, max_results, config)
      → openrouter_academic_search(query, api_key, model, domains)
          → POST https://openrouter.ai/api/v1/chat/completions
            tool: openrouter:web_search, engine: exa
            prompt: return JSON array of papers
          → parse JSON → list[Paper] with source=OPENROUTER_WEB
      → return list[Paper]
  → aggregator receives papers as if Scholar returned them
```

---

## Testing

**`test_openrouter_fallback.py`** (all mocked, no HTTP):
- `test_returns_papers_on_valid_json_response` — mock httpx returns valid JSON array; assert Papers built correctly
- `test_returns_empty_on_http_error` — mock httpx raises; assert `[]` returned, no exception raised
- `test_returns_empty_on_no_api_key` — no key in config/env; assert `[]` and warning logged
- `test_returns_empty_on_malformed_json` — LLM response has no parseable array; assert `[]`
- `test_doi_extracted_when_present` — DOI in JSON used as `Paper.doi` and `Paper.id`
- `test_fallback_uses_env_var_key` — env `OPENROUTER_API_KEY` set; assert it's used
- `test_paper_source_is_openrouter_web` — assert `paper.source == PaperSource.OPENROUTER_WEB`

**`test_google_scholar_playwright.py`** additions:
- `test_captcha_triggers_openrouter_fallback` — mock `_render_and_extract_cards` to return sentinel; mock `openrouter_academic_search` to return 2 papers; assert `search()` returns those 2 papers
- `test_captcha_fallback_disabled_returns_empty` — `openrouter_fallback_enabled=False`; assert `search()` returns `[]` without calling fallback

---

## Non-Goals

- This does NOT add a standalone `OpenRouterSearchProvider` to the aggregator (separate decision).
- This does NOT handle Scholar rate-limits or IP blocks beyond the CAPTCHA/unusual-traffic detection strings already in the code.
- This does NOT use the deprecated `:online` model suffix or the old `plugins` API.
- Perplexity models are not supported via `openrouter:web_search` (OpenRouter limitation); `perplexity/sonar` would require the Perplexity API directly.
