# Agent-CLI token usage parsing — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Parse `usage.input_tokens` / `usage.output_tokens` from
agent-CLI JSON output and feed them into the provenance collector
instead of zeros.

**Spec:** `docs/superpowers/specs/2026-05-14-agent-cli-token-usage-design.md`

---

## Task 1: Config fields

**Files:**
- Modify: `src/perspicacite/config/schema.py` (`LLMProviderConfig` class, around line 232 after `output_file_flag`)
- Test: `tests/unit/test_agent_cli_usage_config_fields.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_cli_usage_config_fields.py
"""Tests for the two usage-path fields on LLMProviderConfig (Wave 2.3)."""
from perspicacite.config.schema import LLMProviderConfig


def test_usage_paths_default_none():
    cfg = LLMProviderConfig()
    assert cfg.usage_input_tokens_path is None
    assert cfg.usage_output_tokens_path is None


def test_usage_paths_accept_strings():
    cfg = LLMProviderConfig(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    assert cfg.usage_input_tokens_path == "usage.input_tokens"
    assert cfg.usage_output_tokens_path == "usage.output_tokens"
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_agent_cli_usage_config_fields.py -v
```

- [ ] **Step 3: Add the fields**

After the `output_file_flag` field block in `LLMProviderConfig`
(around line 232) and before the `cwd` field, insert:

```python
    usage_input_tokens_path: str | None = Field(
        default=None,
        description=(
            "Dotted JSON path to input-token count in the CLI's JSON "
            "output (e.g. 'usage.input_tokens'). Only used when "
            "output_format='json'. None → counts stay at 0 in "
            "provenance (today's behaviour). Wave 2.3."
        ),
    )
    usage_output_tokens_path: str | None = Field(
        default=None,
        description=(
            "Dotted JSON path to output-token count. Same rules as "
            "usage_input_tokens_path."
        ),
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_agent_cli_usage_config_fields.py -v
pytest tests/integration/test_config_audit.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_agent_cli_usage_config_fields.py
git commit -m "feat(config): usage_{input,output}_tokens_path on LLMProviderConfig (Wave 2.3)"
```

---

## Task 2: AgentCLIClient — parse + plumb usage

**Files:**
- Modify: `src/perspicacite/llm/agent_cli.py`
- Test: `tests/unit/test_agent_cli_usage_parsing.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_cli_usage_parsing.py
"""Usage-path parsing in AgentCLIClient (Wave 2.3)."""
import json

import pytest

from perspicacite.llm.agent_cli import AgentCLIClient


def _client(**kw):
    """Build a minimal client just to exercise parsing methods."""
    defaults = dict(
        executable="/bin/true",
        output_format="json",
        result_json_path="result",
    )
    defaults.update(kw)
    return AgentCLIClient(**defaults)


def test_parses_usage_when_paths_set_and_json_valid():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hello",
        "usage": {"input_tokens": 42, "output_tokens": 7},
    })
    text, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert text == "hello"
    assert usage_in == 42
    assert usage_out == 7


def test_partial_hit_uses_zero_for_missing_path():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": 5},  # output_tokens missing
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert usage_in == 5
    assert usage_out == 0


def test_no_paths_returns_zero_zero():
    cli = _client()  # no usage paths
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert usage_in == 0
    assert usage_out == 0


def test_non_json_output_returns_zero_zero():
    cli = _client(
        output_format="text",
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    text, usage_in, usage_out = cli._parse_output_with_usage("plain text output")
    assert text == "plain text output"
    assert (usage_in, usage_out) == (0, 0)


def test_path_resolves_to_non_int_returns_zero():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": "not-a-number", "output_tokens": [1, 2]},
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert (usage_in, usage_out) == (0, 0)


def test_malformed_json_returns_zero_zero():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    text, usage_in, usage_out = cli._parse_output_with_usage("not-json {{{")
    # text falls through to raw, usage is 0/0
    assert text == "not-json {{{"
    assert (usage_in, usage_out) == (0, 0)
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_agent_cli_usage_parsing.py -v
```

Expected: `AttributeError: 'AgentCLIClient' object has no attribute '_parse_output_with_usage'`.

- [ ] **Step 3: Refactor and wire usage**

In `src/perspicacite/llm/agent_cli.py`:

**3a.** Add two parameters to `__init__` (after `output_file_flag`,
before `timeout`):

```python
        usage_input_tokens_path: str | None = None,
        usage_output_tokens_path: str | None = None,
```

And store them:

```python
        self.usage_input_tokens_path = usage_input_tokens_path
        self.usage_output_tokens_path = usage_output_tokens_path
```

**3b.** Add a new method on the class (just below `_parse_output`):

```python
    def _parse_output_with_usage(self, raw: str) -> tuple[str, int, int]:
        """Return ``(assistant_text, input_tokens, output_tokens)``.

        Backwards compat: ``_parse_output`` still returns just the
        text. This wider variant is used by :meth:`complete` so the
        provenance row records honest counts.

        Zeros are returned when:
        - ``output_format != "json"`` (no payload to walk).
        - No usage paths configured.
        - The JSON is malformed.
        - A path resolves to a non-int value.
        """
        text = self._parse_output(raw)
        if self.output_format != "json":
            return text, 0, 0
        if not (self.usage_input_tokens_path or self.usage_output_tokens_path):
            return text, 0, 0
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return text, 0, 0

        def _walk_int(path: str | None) -> int:
            if not path:
                return 0
            v = _walk_json_path(payload, path)
            if isinstance(v, bool):  # bools are ints in Python — exclude
                return 0
            if isinstance(v, int):
                return v
            return 0

        return (
            text,
            _walk_int(self.usage_input_tokens_path),
            _walk_int(self.usage_output_tokens_path),
        )
```

**3c.** In `complete()`, replace the existing `text = self._parse_output(raw)`
line with:

```python
        text, in_tokens, out_tokens = self._parse_output_with_usage(raw)
```

And update the `add_llm_call(...)` call (further down) to pass these:

```python
                _c.add_llm_call(
                    stage_label=stage,
                    provider=self.provider_label,
                    model=resolved_model or "default",
                    prompt_messages=messages,
                    response_text=text,
                    prompt_tokens=in_tokens,
                    completion_tokens=out_tokens,
                    latency_ms=latency_ms,
                )
```

(replacing the existing zeros).

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_agent_cli_usage_parsing.py -v
```

Expected: 6 PASSED.

Also re-run any existing agent_cli unit tests to make sure no regression:

```bash
pytest tests/unit/ -k "agent_cli or claude_cli" -v --timeout=15 --timeout-method=signal
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/agent_cli.py tests/unit/test_agent_cli_usage_parsing.py
git commit -m "feat(agent-cli): parse usage tokens from JSON output (Wave 2.3)"
```

---

## Task 3: Wire config → client

**Files:**
- Modify: `src/perspicacite/llm/client.py` (the `_get_agent_cli_client` method, around line 165)
- Test: `tests/unit/test_agent_cli_client_construction.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_cli_client_construction.py
"""Verify config → AgentCLIClient wiring for usage paths (Wave 2.3)."""
from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient


def test_usage_paths_flow_from_config_to_client():
    cfg = LLMConfig(
        default_provider="agent_cli",
        default_model="haiku",
        providers={
            "agent_cli": LLMProviderConfig(
                executable="/bin/echo",
                output_format="json",
                result_json_path="result",
                usage_input_tokens_path="usage.input_tokens",
                usage_output_tokens_path="usage.output_tokens",
            ),
        },
    )
    client = AsyncLLMClient(cfg)
    cli = client._get_agent_cli_client("agent_cli")
    assert cli.usage_input_tokens_path == "usage.input_tokens"
    assert cli.usage_output_tokens_path == "usage.output_tokens"
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_agent_cli_client_construction.py -v
```

- [ ] **Step 3: Add the kwargs to the AgentCLIClient construction**

In `src/perspicacite/llm/client.py`, in `_get_agent_cli_client`,
inside the `else` branch that constructs the non-claude-cli path
(around line 210-227), add two more kwargs to the
`AgentCLIClient(...)` constructor call:

```python
            client = AgentCLIClient(
                executable=cli_cfg.executable,
                provider_label=provider,
                prompt_via=cli_cfg.prompt_via,
                prompt_flag=cli_cfg.prompt_flag,
                system_flag=cli_cfg.system_flag,
                model_flag=cli_cfg.model_flag,
                extra_args=list(cli_cfg.extra_args),
                output_format=cli_cfg.output_format,
                result_json_path=cli_cfg.result_json_path,
                output_file_flag=cli_cfg.output_file_flag,
                usage_input_tokens_path=cli_cfg.usage_input_tokens_path,
                usage_output_tokens_path=cli_cfg.usage_output_tokens_path,
                timeout=float(cli_cfg.timeout),
                cwd=cli_cfg.cwd,
                env_extra=dict(cli_cfg.env_extra),
                model_aliases=dict(cli_cfg.model_aliases),
            )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_agent_cli_client_construction.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_agent_cli_client_construction.py
git commit -m "feat(llm-client): plumb usage paths into AgentCLIClient (Wave 2.3)"
```

---

## Task 4: Update Claude Code preset + docs

**Files:**
- Modify: `config.claude_code.example.yml`
- Modify: `docs/agent-cli-caveats.md`

- [ ] **Step 1: Update the preset**

In `config.claude_code.example.yml`, find the `agent_cli` provider
block (or `claude_cli` if that's what it uses — both reach the same
client) and add the two new fields. Read the file first to find the
correct insertion point. Add after `output_format: json` or
`result_json_path: result`:

```yaml
      # Wave 2.3: report honest token counts in provenance. Claude
      # Code surfaces these in its `--output-format json` payload.
      usage_input_tokens_path:  "usage.input_tokens"
      usage_output_tokens_path: "usage.output_tokens"
```

Note: the Claude Code preset uses `claude_cli` provider, which
constructs through `ClaudeCLIClient` factory (not the generic
agent_cli branch). For Claude CLI specifically, **also** check
`src/perspicacite/llm/claude_cli.py` — if it has its own constructor,
the usage paths may need to be plumbed there too. If `ClaudeCLIClient`
takes `**kw` and forwards to `AgentCLIClient`, this lands automatically.
Read the file first.

If `ClaudeCLIClient` needs an additional change to accept these
kwargs, update it in this same task and re-run the unit suite to
confirm no regression.

- [ ] **Step 2: Update the caveats doc**

In `docs/agent-cli-caveats.md`, find the "Token usage" or "Cost
accounting" section if present. Otherwise, add a short new subsection
near the top of the per-CLI notes:

```markdown
## Token usage in provenance (Wave 2.3+)

`AgentCLIClient` can extract input / output token counts from the
CLI's JSON output when `usage_input_tokens_path` and
`usage_output_tokens_path` are set in the provider config. Today:

| CLI | Status |
|---|---|
| Claude Code | ✅ Live — preset wires `usage.input_tokens` / `usage.output_tokens` |
| Codex | ❌ Out — `--output-last-message` returns plain text. Would require switching to `--json` event-stream parsing (followup). |
| OpenClaw | ❓ Unverified — set paths in config if known |
| Hermes | ❓ Unverified — set paths in config if known |

When paths are unset or the CLI doesn't surface usage, provenance
records `prompt_tokens=0, completion_tokens=0` (today's behaviour, no
regression). Budget caps (Wave 2.4) treat those as "unknown cost".
```

- [ ] **Step 3: Commit**

```bash
git add config.claude_code.example.yml docs/agent-cli-caveats.md src/perspicacite/llm/claude_cli.py
git commit -m "docs+preset(agent-cli): wire usage paths for Claude Code (Wave 2.3)"
```

(Only include `src/perspicacite/llm/claude_cli.py` in the commit if
you actually modified it.)

---

## Done

After Task 4: usage tokens flow honestly into provenance whenever the
CLI emits them and the config requests them. Codex (`--output-last-message`
mode) stays at 0/0 with a documented followup.
