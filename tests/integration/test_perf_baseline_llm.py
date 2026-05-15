"""Live synthesis-stage perf baseline (Wave 6.3 + F7 of audit 2026-05-15).

Wave 6.3's main `test_perf_baseline.py` mocks the LLM, so a regression in
the real synthesis path (e.g. re-introducing the retry-on-AuthError bug
from F1) wouldn't trip the perf gate. This file runs a small real LLM
synthesis and asserts it stays within tolerance of a stored baseline.

Marked ``live + perf`` — only runs when explicitly selected. CI should
skip by default; nightly / release pipelines opt in.

Knobs (env vars):

- ``PERSPICACITE_PERF_LLM_PROVIDER``  one of ``anthropic``, ``claude_cli``
  (default ``claude_cli`` — uses the user's Claude Code subscription,
  no API key needed).
- ``PERSPICACITE_UPDATE_PERF_LLM_BASELINE=1``  regenerate the baseline.
- ``PERSPICACITE_PERF_LLM_TOLERANCE``  default ``2.0`` (latency varies
  more than throughput).
- ``PERSPICACITE_SKIP_LIVE_LLM_PERF=1``  bail without running (CI-friendly).

Baseline lives at ``tests/data/perf_baseline_llm.json``.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.perf]


BASELINE_PATH = Path(__file__).parent.parent / "data" / "perf_baseline_llm.json"
UPDATE = os.environ.get("PERSPICACITE_UPDATE_PERF_LLM_BASELINE") == "1"
SKIP = os.environ.get("PERSPICACITE_SKIP_LIVE_LLM_PERF") == "1"
TOLERANCE = float(os.environ.get("PERSPICACITE_PERF_LLM_TOLERANCE", "2.0"))
PROVIDER = os.environ.get("PERSPICACITE_PERF_LLM_PROVIDER", "claude_cli")


SYSTEM_PROMPT = (
    "You are a research synthesizer. Given a question and a paper abstract, "
    "produce a 2-paragraph summary that answers the question. Cite the paper "
    "by DOI in square brackets."
)

USER_PROMPT = (
    "Question: What does retrieval-augmented generation do?\n\n"
    "Abstract [10.0001/lewis2020rag]: Large pre-trained language models have "
    "been shown to store factual knowledge in their parameters, but their "
    "ability to access and manipulate it is limited. We propose retrieval-"
    "augmented generation (RAG), a hybrid approach combining a parametric "
    "memory (a seq2seq model) with a non-parametric memory (a dense vector "
    "index of Wikipedia)."
)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def _make_config():
    from perspicacite.config.schema import Config, LLMConfig, LLMProviderConfig
    cfg = Config()
    if PROVIDER == "anthropic":
        cfg.llm = LLMConfig(
            default_provider="anthropic",
            default_model="claude-haiku-4-5",
            cache_enabled=False,
            providers={
                "anthropic": LLMProviderConfig(
                    base_url="https://api.anthropic.com",
                    timeout=60,
                    max_retries=1,
                ),
            },
        )
    else:
        cfg.llm = LLMConfig(
            default_provider="claude_cli",
            default_model="haiku",
            cache_enabled=False,
            providers={
                "claude_cli": LLMProviderConfig(
                    base_url="",
                    timeout=120,
                    max_retries=1,
                    executable="claude",
                    prompt_via="stdin",
                    prompt_flag=None,
                    extra_args=["--print", "--output-format", "json"],
                    system_flag="--system-prompt",
                    model_flag="--model",
                    output_format="json",
                    output_text_path="result",
                    usage_input_tokens_path="usage.input_tokens",
                    usage_output_tokens_path="usage.output_tokens",
                    cost_usd_path="total_cost_usd",
                ),
            },
        )
    return cfg


@pytest.mark.asyncio
async def test_perf_baseline_llm():
    if SKIP:
        pytest.skip("PERSPICACITE_SKIP_LIVE_LLM_PERF set")
    if PROVIDER == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY missing — set PERSPICACITE_PERF_LLM_PROVIDER=claude_cli")
    if PROVIDER == "claude_cli":
        # Quick check that `claude` is on PATH.
        import shutil
        if shutil.which("claude") is None:
            pytest.skip("`claude` CLI not on PATH")

    from perspicacite.llm.client import AsyncLLMClient
    cfg = _make_config()
    client = AsyncLLMClient(cfg.llm)

    # Warmup (a cold first call dominates timing on agent_cli).
    try:
        await client.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Say OK."},
            ],
            cache=False,
            max_tokens=10,
        )
    except Exception:
        pytest.skip("Warmup call failed — environment not ready for live LLM perf test")

    # Timed run × 2
    timings_s: list[float] = []
    output_lens: list[int] = []
    for _ in range(2):
        t0 = time.perf_counter()
        out = await client.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT},
            ],
            cache=False,
            max_tokens=400,
            temperature=0.3,
        )
        timings_s.append(time.perf_counter() - t0)
        output_lens.append(len(out))

    metrics = {
        "provider": PROVIDER,
        "synthesis_seconds_avg": round(sum(timings_s) / len(timings_s), 3),
        "synthesis_seconds_min": round(min(timings_s), 3),
        "synthesis_seconds_max": round(max(timings_s), 3),
        "output_chars_avg": int(sum(output_lens) / len(output_lens)),
        "git_sha": _git_sha(),
        "timestamp": time.time(),
    }

    if UPDATE or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(metrics, indent=2) + "\n")
        pytest.skip(
            f"Live-LLM baseline written to {BASELINE_PATH} "
            "(re-run without PERSPICACITE_UPDATE_PERF_LLM_BASELINE)."
        )

    baseline = json.loads(BASELINE_PATH.read_text())
    # Provider switch invalidates the baseline.
    if baseline.get("provider") != PROVIDER:
        pytest.skip(
            f"Stored baseline is for provider={baseline.get('provider')}, "
            f"current run is {PROVIDER} — re-capture with "
            "PERSPICACITE_UPDATE_PERF_LLM_BASELINE=1."
        )

    cur = metrics["synthesis_seconds_avg"]
    base = float(baseline.get("synthesis_seconds_avg", cur))
    ratio = cur / max(base, 1e-9)
    if ratio > TOLERANCE:
        pytest.fail(
            f"Live synthesis perf regressed: current={cur:.2f}s, "
            f"baseline={base:.2f}s ({ratio:.2f}× slower, tolerance={TOLERANCE})"
        )
    print(
        f"Live synthesis perf: current={cur:.2f}s, baseline={base:.2f}s "
        f"({ratio:.2f}× of baseline; output chars avg={metrics['output_chars_avg']})"
    )
