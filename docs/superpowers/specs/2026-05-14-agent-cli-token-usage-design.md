# Token-usage parsing for agent_cli — design spec

**Wave 2.3 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Parse `usage.input_tokens` / `usage.output_tokens` (or
provider-equivalent paths) out of agent-CLI JSON output so cost
accounting stops being a lie (0 / 0 today) when routing through
Claude Code / Codex / OpenClaw / Hermes.

## Why now

Wave 2.4 (budget caps) needs honest token counts. Without this fix,
budget caps will only see direct-API calls, which defeats their
purpose on subscription-routed setups.

## Approach

Add two optional config fields to `LLMProviderConfig`:

| Field | Purpose | Default |
|---|---|---|
| `usage_input_tokens_path`  | Dotted JSON path to input token count in JSON output  | `None` (counts as 0) |
| `usage_output_tokens_path` | Dotted JSON path to output token count in JSON output | `None` (counts as 0) |

Only effective when `output_format == "json"` and the CLI's JSON
payload contains the requested paths.

`_parse_output` is split into:
- `_parse_payload(raw) -> dict | None` — parse JSON once.
- `_extract_text(payload, raw) -> str` — pick the assistant text
  (unchanged logic).
- `_extract_usage(payload) -> (int, int)` — walk the new paths.

`complete()` then writes the extracted counts into the provenance
collector instead of zeros.

## Per-CLI status

| CLI | JSON shape | usage paths |
|---|---|---|
| Claude Code | `{"result": "...", "usage": {"input_tokens": N, "output_tokens": N, ...}, ...}` | `usage.input_tokens`, `usage.output_tokens` |
| Codex | Streamed JSON events when `--json` set. Our current preset uses `--output-last-message` which writes plain text. To get usage we'd need to switch to event-stream parsing. **Out of scope for v2.3**; documented as known limitation. |
| OpenClaw | (no live verification yet) — config can be set per user when known. |
| Hermes | Same. |

So v2.3 lights up usage tracking for the most common case (Claude Code)
and prepares the plumbing for the rest.

## Components

| File | Change |
|---|---|
| `src/perspicacite/config/schema.py` | Add 2 new optional fields. |
| `src/perspicacite/llm/agent_cli.py` | Refactor `_parse_output` into text + usage. Plumb usage into `add_llm_call`. |
| `src/perspicacite/llm/client.py` | Pass usage paths through when constructing `AgentCLIClient`. |
| `config.claude_code.example.yml` | Document the two new fields with Claude Code defaults. |
| `tests/unit/test_agent_cli_usage_parsing.py` (new) | 6 tests covering: hit, partial hit, miss, no JSON, no paths configured, malformed payload. |

## Behaviour contract

- If both paths are unset OR `output_format != "json"`: usage stays
  at `(0, 0)` (today's behaviour, no regression).
- If both paths are set and the JSON payload yields ints: use those
  values.
- If one path resolves and the other doesn't: use what we have, the
  missing one is 0 (better than dropping both).
- If a path resolves to a non-int value (e.g., a list / string):
  fall back to 0 for that side, log a warning once per process.

## Test plan

- `test_parses_usage_when_paths_set_and_json_valid`
- `test_partial_hit_uses_zero_for_missing_path`
- `test_no_paths_returns_zero_zero`
- `test_non_json_output_returns_zero_zero`
- `test_path_resolves_to_non_int_returns_zero_warns`
- `test_codex_text_path_returns_zero_zero` (legitimate today)

Live-verification (manual, not in CI): run a Claude Code call through
`AsyncLLMClient` with provenance enabled, confirm the provenance row
shows non-zero `prompt_tokens` / `completion_tokens`.

## Followups

- Codex event-stream parser (sub-project Wave 2.3a). Would replace
  `--output-last-message` with `--json` + a small stream reader.
- Cost-per-token estimation from a small static table
  (`anthropic/claude-haiku-4-5` $.80/M in, $4/M out, etc.) so budget
  caps in 2.4 can express dollars not just tokens.
