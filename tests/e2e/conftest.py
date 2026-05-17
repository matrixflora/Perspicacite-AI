"""Shared fixtures for E2E pipeline tests (Wave 6.1).

Goal: deterministic, fast (<10s for the suite), no network. Mocks the
LLM and the embedding provider so the full pipeline runs against
tmp_path-only storage.

The ``synthetic_paper`` / ``synthetic_corpus`` fixtures return real
``Paper`` instances so the tests can hand them directly to
``DynamicKnowledgeBase.add_papers`` — that method's signature takes
list[Paper] (see src/perspicacite/rag/dynamic_kb.py).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

# Skip the entire e2e folder cleanly if chromadb / numpy aren't available.
chromadb = pytest.importorskip("chromadb")
np = pytest.importorskip("numpy")

from perspicacite.models.papers import Author, Paper, PaperSource

# ---------------------------------------------------------------------------
# Deterministic mocks
# ---------------------------------------------------------------------------

def _deterministic_vec(text: str, dim: int) -> list[float]:
    """SHA-256-derived vector — same text always returns same vector.

    We hash the text, then repeat the digest bytes until we have ``dim``
    floats in roughly [-1, 1]. Vectors are normalized to unit length so
    cosine-distance ranking is well-defined (Chroma's hnsw:space=cosine
    expects this).
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    floats: list[float] = []
    while len(floats) < dim:
        for b in h:
            floats.append((b / 127.5) - 1.0)
            if len(floats) >= dim:
                break
        # Re-hash so we get more entropy past 32 bytes
        h = hashlib.sha256(h).digest()
    arr = np.asarray(floats, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


class DeterministicEmbeddingProvider:
    """In-memory, deterministic, no-IO embedding provider.

    Same text always returns the same vector. Cosine-normalised so it
    plays well with Chroma's cosine collections.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim
        self.calls = 0
        self.total_texts = 0

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "deterministic-mock"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.total_texts += len(texts)
        return [_deterministic_vec(t, self._dim) for t in texts]


class StagedLLM:
    """Returns canned strings keyed by ``stage`` kwarg.

    Records every call. The default response is interpolated against
    the last message so report-synthesis tests can still grep for
    paper-specific tokens.
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict] | None = None, **kwargs) -> str:
        msgs = messages or kwargs.get("messages") or []
        stage = kwargs.get("stage", "default")
        self.calls.append({"stage": stage, "messages": msgs, "kwargs": kwargs})
        canned = self.responses.get(stage)
        if canned is not None:
            return canned
        # Echo the user content so tests can search for it.
        last_user = next(
            (m for m in reversed(msgs) if (m or {}).get("role") == "user"), None,
        )
        body = (last_user or {}).get("content", "")
        if isinstance(body, list):
            body = " ".join(
                part.get("text", "") for part in body if isinstance(part, dict)
            )
        return f"[mock:{stage}] {body[:300]}"

    async def stream(self, messages=None, **kwargs):
        text = await self.complete(messages, **kwargs)
        for tok in text.split():
            yield tok + " "


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def deterministic_embedder() -> DeterministicEmbeddingProvider:
    """Fresh deterministic embedder per test (so .calls counters reset)."""
    return DeterministicEmbeddingProvider()


@pytest.fixture
def staged_llm() -> StagedLLM:
    return StagedLLM()


def _make_paper(
    doi: str, title: str, abstract: str, *, year: int = 2024,
) -> Paper:
    return Paper(
        id=f"doi:{doi}",
        doi=doi,
        title=title,
        authors=[Author(name="Mock Author", family="Author")],
        year=year,
        abstract=abstract,
        full_text=(title + ". " + abstract + " ") * 30,
        source=PaperSource.WEB_SEARCH,
    )


@pytest.fixture
def synthetic_paper() -> Paper:
    return _make_paper(
        doi="10.0001/synthetic",
        title="On the formation of red giants in low-metallicity environments",
        abstract=(
            "We model the late-stage evolution of low-metallicity stars and "
            "find that red-giant formation rates scale inversely with "
            "metallicity. We use Monte-Carlo stellar-evolution simulations."
        ),
        year=2025,
    )


@pytest.fixture
def synthetic_corpus() -> list[Paper]:
    """5 papers, 2 astro, 2 bio, 1 cross-disciplinary."""
    base = [
        ("10.0001/a1", "Stellar nucleosynthesis in massive stars",
         "Stellar physics, supernova ejecta, heavy elements, red giants."),
        ("10.0001/a2", "Red giant branch evolution",
         "Helium-burning shells, asymptotic giant branch, mass loss, stellar."),
        ("10.0001/b1", "AlphaFold-2 predictions of GPCR structures",
         "Protein folding, structure prediction, transmembrane domains, alphafold."),
        ("10.0001/b2", "Cryo-EM of ribosome assembly intermediates",
         "Ribosome biogenesis, protein structure, RNA folding, cryo-em."),
        ("10.0001/x1", "Astrobiology: searching for biosignatures on exoplanets",
         "Exoplanets, biosignatures, atmospheric spectroscopy, protein chemistry."),
    ]
    return [_make_paper(doi, title, abstract) for (doi, title, abstract) in base]


@pytest.fixture
def tmp_kb_root(tmp_path: Path) -> Path:
    """Returns a tmp_path subdir for KB storage."""
    root = tmp_path / "kb_root"
    root.mkdir(parents=True, exist_ok=True)
    return root
