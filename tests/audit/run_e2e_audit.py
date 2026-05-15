"""End-to-end real-LLM audit harness.

Spec: pick two example queries, run both through the real
Perspicacité pipeline against (a) the Anthropic API and (b) the
local `claude` CLI, capture outputs, costs, errors. Used after Wave 7
shipped to identify residual issues before the framework-hardening
roadmap is declared done.

This is NOT a pytest test — it's a script. Run as:

    python tests/audit/run_e2e_audit.py --provider api      > audit-api.log
    python tests/audit/run_e2e_audit.py --provider claude   > audit-cli.log

Outputs:
- stdout: per-example timing + output preview
- JSON artefact: tests/audit/results/<provider>-<ts>.json with full payloads
- Audit notes: tests/audit/results/<provider>-<ts>.notes.md (observations)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

# Make repo importable when run as a script.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from perspicacite.config.schema import (
    Config,
    LLMConfig,
    LLMProviderConfig,
    KnowledgeBaseConfig,
)
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.embeddings import create_embedding_provider


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True, parents=True)


# ---------------------------------------------------------------------------
# Examples — two queries from different domains
# ---------------------------------------------------------------------------

EXAMPLES = [
    {
        "id": "ex_a",
        "name": "Retrieval-Augmented Generation",
        "context_papers": [
            {
                "doi": "10.0001/lewis2020rag",
                "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                "abstract": (
                    "Large pre-trained language models have been shown to store factual "
                    "knowledge in their parameters, but their ability to access and "
                    "manipulate it is limited. We propose retrieval-augmented generation "
                    "(RAG), a hybrid approach combining a parametric memory (a seq2seq "
                    "model) with a non-parametric memory (a dense vector index of "
                    "Wikipedia). RAG models outperform parametric-only baselines across "
                    "knowledge-intensive tasks and produce more specific, diverse, and "
                    "factual responses."
                ),
                "year": 2020,
            },
            {
                "doi": "10.0001/borgeaud2022retro",
                "title": "Improving Language Models by Retrieving from Trillions of Tokens",
                "abstract": (
                    "We enhance auto-regressive language models by conditioning on "
                    "document chunks retrieved from a large corpus based on local "
                    "similarity with the preceding tokens. With a 2 trillion token "
                    "database, our Retrieval-Enhanced Transformer (RETRO) obtains "
                    "comparable performance to GPT-3 on the Pile, despite using 25× "
                    "fewer parameters. Performance gains hold across multiple model "
                    "sizes."
                ),
                "year": 2022,
            },
            {
                "doi": "10.0001/izacard2022atlas",
                "title": "Atlas: Few-shot Learning with Retrieval Augmented Language Models",
                "abstract": (
                    "Large language models have shown impressive few-shot learning "
                    "abilities, but they fall short on knowledge-intensive tasks. Atlas "
                    "couples a sequence-to-sequence model with a learned retriever and "
                    "is jointly fine-tuned end-to-end. With far fewer parameters than "
                    "competing methods, Atlas attains state-of-the-art results on KILT "
                    "and other QA benchmarks under both few-shot and full-finetuning "
                    "regimes."
                ),
                "year": 2022,
            },
        ],
        "query": (
            "How does retrieval-augmented generation reduce the need for parametric "
            "memory in language models, and what are the main architectural variants?"
        ),
    },
    {
        "id": "ex_b",
        "name": "Protein structure prediction",
        "context_papers": [
            {
                "doi": "10.0002/jumper2021alphafold2",
                "title": "Highly accurate protein structure prediction with AlphaFold",
                "abstract": (
                    "Proteins are essential to life, and understanding their structure "
                    "facilitates a mechanistic understanding of function. AlphaFold "
                    "predicts protein structures with atomic accuracy, matching "
                    "experimental data in many cases. The system combines a novel "
                    "neural-network architecture and training procedure based on the "
                    "evolutionary, physical and geometric constraints of protein "
                    "structures."
                ),
                "year": 2021,
            },
            {
                "doi": "10.0002/baek2021rosettafold",
                "title": "Accurate prediction of protein structures and interactions using RoseTTAFold",
                "abstract": (
                    "We describe RoseTTAFold, a three-track neural network that "
                    "processes 1D sequence, 2D distance and 3D coordinate information. "
                    "RoseTTAFold produces accurate protein structure predictions with "
                    "fewer compute cycles than AlphaFold-2 and accurately predicts "
                    "protein-protein complexes from sequence alone."
                ),
                "year": 2021,
            },
            {
                "doi": "10.0002/lin2023esmfold",
                "title": "Evolutionary-scale prediction of atomic-level protein structure",
                "abstract": (
                    "ESMFold uses a language model trained on the evolutionary record "
                    "of protein sequences to predict three-dimensional structure "
                    "directly from a single sequence, bypassing the multiple-sequence-"
                    "alignment requirement of AlphaFold-2. ESMFold is up to 60x faster "
                    "than AlphaFold-2 at comparable accuracy on short sequences."
                ),
                "year": 2023,
            },
        ],
        "query": (
            "Compare AlphaFold-2, RoseTTAFold, and ESMFold along the axes of speed, "
            "accuracy, and reliance on multiple sequence alignments."
        ),
    },
]


# ---------------------------------------------------------------------------
# Build retrieval context (skip the real ChromaDB ingest — we feed
# context papers directly to the LLM as a synthesis-only test).
# ---------------------------------------------------------------------------

def _format_context(papers: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for p in papers:
        parts.append(
            f"[{p['doi']}] {p['title']} ({p['year']})\n"
            f"Abstract: {p['abstract']}\n"
        )
    return "\n---\n".join(parts)


SYSTEM_PROMPT = (
    "You are a careful research synthesizer. Given a question and a set of "
    "paper abstracts, produce a 4-6 paragraph synthesis that answers the "
    "question. Cite every claim by DOI in square brackets, e.g. [10.0001/foo]. "
    "If the abstracts are insufficient to answer, say so explicitly."
)


def _user_message(query: str, papers: list[dict]) -> str:
    return (
        f"Question:\n{query}\n\n"
        f"Available abstracts:\n{_format_context(papers)}\n\n"
        f"Write a 4-6 paragraph synthesis citing each abstract by its DOI."
    )


# ---------------------------------------------------------------------------
# Provider configs
# ---------------------------------------------------------------------------

def make_api_config() -> Config:
    """Direct Anthropic API for synthesis."""
    cfg = Config()
    cfg.llm = LLMConfig(
        default_provider="anthropic",
        default_model="claude-sonnet-4-5",
        cache_enabled=True,
        cache_path=ROOT / "data" / "audit_llm_cache.db",
        cache_ttl_hours=24,
        providers={
            "anthropic": LLMProviderConfig(
                base_url="https://api.anthropic.com",
                timeout=120,
                max_retries=2,
            ),
        },
    )
    cfg.knowledge_base = KnowledgeBaseConfig(
        embedding_model="all-MiniLM-L6-v2",  # local, no OPENAI key needed
    )
    return cfg


def make_cli_config() -> Config:
    """Claude Code CLI for synthesis."""
    cfg = Config()
    cfg.llm = LLMConfig(
        default_provider="claude_cli",
        default_model="sonnet",
        cache_enabled=True,
        cache_path=ROOT / "data" / "audit_llm_cache_cli.db",
        cache_ttl_hours=24,
        providers={
            "claude_cli": LLMProviderConfig(
                base_url="",
                timeout=300,
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
            ),
        },
    )
    cfg.knowledge_base = KnowledgeBaseConfig(
        embedding_model="all-MiniLM-L6-v2",
    )
    return cfg


# ---------------------------------------------------------------------------
# Audit runner
# ---------------------------------------------------------------------------

async def run_example(client: AsyncLLMClient, example: dict, *, provider_name: str) -> dict:
    """Run one synthesis and capture timing + output + errors."""
    messages = [
        {"role": "user", "content": _user_message(example["query"], example["context_papers"])},
    ]

    record: dict[str, Any] = {
        "example_id": example["id"],
        "example_name": example["name"],
        "query": example["query"],
        "provider": provider_name,
        "n_context_papers": len(example["context_papers"]),
        "ok": False,
        "error": None,
        "elapsed_s": None,
        "output": None,
        "output_chars": 0,
        "system_prompt": SYSTEM_PROMPT,
    }

    t0 = time.perf_counter()
    try:
        # We bypass the disk cache for the audit (we want fresh runs).
        out = await client.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *messages,
            ],
            max_tokens=2048,
            temperature=0.3,
            cache=False,
            stage="audit",
        )
        record["ok"] = True
        record["output"] = out
        record["output_chars"] = len(out)
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {e}"
        record["traceback"] = traceback.format_exc()
    finally:
        record["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return record


def _embedding_smoke(provider_name: str) -> dict:
    """Verify the embedder loads (Wave 2.2 path)."""
    rec: dict[str, Any] = {"provider": provider_name, "ok": False}
    try:
        prov = create_embedding_provider(model="all-MiniLM-L6-v2")
        rec["model_name"] = getattr(prov, "model_name", None)
        rec["dimension"] = getattr(prov, "dimension", None)
        rec["ok"] = True
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


async def main_async(provider_choice: str) -> None:
    if provider_choice == "api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set; cannot run --provider api.")
        cfg = make_api_config()
        provider_label = "anthropic_api"
    elif provider_choice == "claude-haiku":
        cfg = make_cli_config()
        cfg.llm.default_model = "haiku"
        provider_label = "claude_cli_haiku"
    else:
        cfg = make_cli_config()
        provider_label = "claude_cli_sonnet"

    client = AsyncLLMClient(cfg.llm)

    print(f"=== E2E audit run: provider={provider_label} ===")
    emb = _embedding_smoke(provider_label)
    print(f"  embedding smoke: {emb}")
    print()

    results = []
    for example in EXAMPLES:
        print(f"--- {example['id']}: {example['name']} ---")
        rec = await run_example(client, example, provider_name=provider_label)
        results.append(rec)
        if rec["ok"]:
            print(f"  OK in {rec['elapsed_s']}s, {rec['output_chars']} chars")
            print(f"  preview: {rec['output'][:300]}...")
        else:
            print(f"  FAILED in {rec['elapsed_s']}s: {rec['error']}")
        print()

    ts = int(time.time())
    artefact_path = RESULTS_DIR / f"{provider_label}-{ts}.json"
    artefact = {
        "provider": provider_label,
        "timestamp": ts,
        "embedding_smoke": emb,
        "results": results,
    }
    artefact_path.write_text(json.dumps(artefact, indent=2))
    print(f"=> wrote {artefact_path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--provider", required=True, choices=("api", "claude", "claude-haiku"),
        help="api = Anthropic API; claude = claude CLI sonnet; claude-haiku = claude CLI haiku",
    )
    args = p.parse_args()
    asyncio.run(main_async(args.provider))


if __name__ == "__main__":
    main()
