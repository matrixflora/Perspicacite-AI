"""HyDE (Hypothetical Document Embeddings) query generation helper.

HyDE bridges the vocabulary gap between colloquial claim language and
domain-specific paper terminology. Instead of searching the KB with the
raw claim text, we first ask the LLM to produce a short synthetic
abstract that would support the claim, then use that richer text as the
vector-search query.

Reference: Gao et al., 2022 — "Precise Zero-Shot Dense Retrieval without
Relevance Labels" (https://arxiv.org/abs/2212.10496)

Usage::

    from perspicacite.rag.modes.hyde_query import generate_hyde_query

    hyde_text = await generate_hyde_query(
        claim=claim,
        llm_client=llm,
        model="claude-haiku-4-5",
        provider="anthropic",
    )
    # use hyde_text as vector-search query instead of claim

The function is intentionally lightweight:
- Single LLM call, ~100 completion tokens.
- Uses the *cheap/fast* model (Haiku / deepseek-v4-flash), NOT the
  synthesis model.
- Falls back silently to the original claim on any LLM error so the
  calling mode never breaks.
"""

from __future__ import annotations

from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.modes.hyde_query")

_SYSTEM_PROMPT = (
    "You are a biomedical literature expert. Your task is to write a concise "
    "scientific abstract excerpt that would directly support a given factual "
    "claim. Use the vocabulary, style, and terminology found in academic papers "
    "— not everyday language."
)

_USER_TEMPLATE = (
    "Claim: {claim}\n\n"
    "Write a 2-3 sentence scientific abstract excerpt that would directly "
    "support this claim. Use the language of scientific papers — include "
    "domain-specific terminology, standard methods or assay names, and "
    "precise quantitative language where relevant.\n\n"
    "Abstract excerpt:"
)


async def generate_hyde_query(
    claim: str,
    llm_client: Any,
    model: str,
    provider: str,
) -> str:
    """Generate a hypothetical paper abstract that would support *claim*.

    Parameters
    ----------
    claim:
        The factual claim / research question to expand.
    llm_client:
        An ``AsyncLLMClient`` instance (``perspicacite.llm.client``).
    model:
        LLM model identifier to use.  Callers should pass a cheap/fast
        model such as ``"claude-haiku-4-5"`` or ``"deepseek-chat"``.
    provider:
        LLM provider string (e.g. ``"anthropic"``, ``"deepseek"``).

    Returns
    -------
    str
        Synthetic abstract excerpt.  On any LLM error the original
        *claim* is returned unchanged so callers are never broken.
    """
    if not claim or not claim.strip():
        return claim

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(claim=claim.strip())},
    ]

    try:
        hyde_text = await llm_client.complete(
            messages=messages,
            model=model,
            provider=provider,
            max_tokens=150,
            temperature=0.4,
            stage="hyde.generate",
        )
        hyde_text = (hyde_text or "").strip()
        if not hyde_text:
            logger.warning("hyde_empty_response", claim_preview=claim[:80])
            return claim
        logger.info(
            "hyde_generated",
            claim_preview=claim[:80],
            hyde_preview=hyde_text[:120],
        )
        return hyde_text
    except Exception as exc:
        logger.warning("hyde_generation_failed", error=str(exc), claim_preview=claim[:80])
        return claim
