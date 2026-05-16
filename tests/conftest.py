"""Test configuration and fixtures.

Note: the ``deterministic_embedder`` fixture (and supporting
``DeterministicEmbeddingProvider`` class) live here — at the top-level
conftest pytest auto-loads first — so both ``tests/e2e/`` and
``tests/integration/`` share a single canonical definition. Registering
the same fixture name in two collected conftests at the same scope
raises ``ValueError`` at collection time, so subordinate conftests
(``tests/e2e/conftest.py``) must NOT redeclare it.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Generator

import numpy as np
import pytest

from perspicacite.config.schema import Config


@pytest.fixture
def config() -> Config:
    """Test configuration with defaults."""
    return Config()


# ---------------------------------------------------------------------------
# Deterministic embedding provider (shared by e2e + integration suites)
# ---------------------------------------------------------------------------


def _deterministic_vec(text: str, dim: int) -> list[float]:
    """SHA-256-derived unit vector. Same text → same vector.

    Hashes the text, then repeats the digest bytes until ``dim`` floats
    in roughly [-1, 1] are available. Vectors are L2-normalised so
    cosine-distance ranking is well-defined (Chroma's
    ``hnsw:space=cosine`` expects unit-norm inputs).
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    floats: list[float] = []
    while len(floats) < dim:
        for b in h:
            floats.append((b / 127.5) - 1.0)
            if len(floats) >= dim:
                break
        # Re-hash so we get more entropy past 32 bytes.
        h = hashlib.sha256(h).digest()
    arr = np.asarray(floats, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


class DeterministicEmbeddingProvider:
    """In-memory, deterministic, no-IO embedding provider.

    Same text always returns the same vector. Cosine-normalised so it
    plays well with Chroma's cosine collections. Exposes ``.calls`` /
    ``.total_texts`` counters that e2e tests assert against.
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


@pytest.fixture
def deterministic_embedder() -> DeterministicEmbeddingProvider:
    """Fresh deterministic embedder per test (so ``.calls`` counters reset)."""
    return DeterministicEmbeddingProvider()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Temporary directory for tests."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def mock_llm_client():
    """Mock LLM client."""
    class MockLLM:
        async def complete(self, messages: list[dict], **kwargs) -> str:
            return "This is a mock LLM response."

        async def stream(self, messages: list[dict], **kwargs):
            for word in "This is a mock LLM response.".split():
                yield word + " "

    return MockLLM()


@pytest.fixture
def sample_papers() -> list[dict]:
    """Sample paper data for tests."""
    return [
        {
            "id": "doi:10.1234/test1",
            "title": "Test Paper 1: Introduction to Testing",
            "authors": [{"name": "John Doe", "family": "Doe"}],
            "year": 2024,
            "doi": "10.1234/test1",
            "abstract": "This is a test paper.",
        },
        {
            "id": "doi:10.1234/test2",
            "title": "Test Paper 2: Advanced Testing Methods",
            "authors": [{"name": "Jane Smith", "family": "Smith"}],
            "year": 2023,
            "doi": "10.1234/test2",
            "abstract": "Another test paper.",
        },
    ]


@pytest.fixture
def mock_embedding_provider():
    """Mock embedding provider that returns random vectors."""
    import numpy as np

    class MockEmbeddingProvider:
        def __init__(self, dimension: int = 384):
            self._dimension = dimension

        @property
        def dimension(self) -> int:
            return self._dimension

        @property
        def model_name(self) -> str:
            return "mock-embeddings"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            """Return random embeddings."""
            return [
                np.random.randn(self._dimension).tolist()
                for _ in texts
            ]

    return MockEmbeddingProvider()
