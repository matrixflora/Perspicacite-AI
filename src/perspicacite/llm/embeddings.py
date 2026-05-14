"""Embedding providers for vector search."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.llm.embeddings")


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts."""
        ...

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        ...

    @property
    def model_name(self) -> str:
        """Return the model name."""
        ...


class LiteLLMEmbeddingProvider:
    """
    Embedding provider using LiteLLM.

    Supports OpenAI, Cohere, Voyage, and other providers via LiteLLM.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        batch_size: int = 32,
    ):
        self.model = model
        self.batch_size = batch_size
        self._litellm = None
        self._dimension = self._get_dimension()

    def _get_litellm(self) -> Any:
        """Lazy import litellm."""
        if self._litellm is None:
            import litellm

            self._litellm = litellm
        return self._litellm

    def _get_dimension(self) -> int:
        """Get embedding dimension for the model."""
        dimensions = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return dimensions.get(self.model, 1536)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self.model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed texts using LiteLLM.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Filter out empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return [[0.0] * self.dimension for _ in texts]

        logger.debug("embedding_start", text_count=len(valid_texts), model=self.model)

        try:
            litellm = self._get_litellm()

            # Process in batches
            all_embeddings = []
            for i in range(0, len(valid_texts), self.batch_size):
                batch = valid_texts[i : i + self.batch_size]

                response = await litellm.aembedding(
                    model=self.model,
                    input=batch,
                )

                batch_embeddings = [item["embedding"] for item in response["data"]]
                all_embeddings.extend(batch_embeddings)

            logger.debug(
                "embedding_complete",
                text_count=len(valid_texts),
                dimension=self.dimension,
            )

            return all_embeddings

        except Exception as e:
            logger.error(
                "embedding_error",
                model=self.model,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise


class SentenceTransformerEmbeddingProvider:
    """
    Local embedding provider using sentence-transformers.

    Falls back to this if API embeddings fail or for offline use.
    """

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        batch_size: int = 32,
        device: str | None = None,
    ):
        self.model_name = model
        self.batch_size = batch_size
        self.device = device or "cpu"
        self._model = None

    def _get_model(self) -> Any:
        """Lazy load the model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(
                    "loading_sentence_transformer",
                    model=self.model_name,
                    device=self.device,
                )
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except ImportError:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        if self._model is None:
            # Common dimensions
            dimensions = {
                "all-MiniLM-L6-v2": 384,
                "all-MiniLM-L12-v2": 384,
                "all-mpnet-base-v2": 768,
            }
            return dimensions.get(self.model_name, 384)
        return self._model.get_sentence_embedding_dimension()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed texts locally.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Filter out empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return [[0.0] * self.dimension for _ in texts]

        logger.debug(
            "local_embedding_start",
            text_count=len(valid_texts),
            model=self.model_name,
        )

        try:
            import asyncio

            model = self._get_model()

            # Run in thread pool since sentence-transformers is CPU-bound
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                None,
                lambda: model.encode(
                    valid_texts,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                ),
            )

            # Convert to list of lists
            embeddings_list = embeddings.tolist()

            logger.debug(
                "local_embedding_complete",
                text_count=len(valid_texts),
                dimension=len(embeddings_list[0]) if embeddings_list else 0,
            )

            return embeddings_list

        except Exception as e:
            logger.error(
                "local_embedding_error",
                model=self.model_name,
                error=str(e),
            )
            raise


class FallbackEmbeddingProvider:
    """
    Embedding provider with automatic fallback.

    Tries primary provider first, falls back to secondary on failure.
    """

    def __init__(
        self,
        primary: EmbeddingProvider,
        fallback: EmbeddingProvider,
    ):
        self.primary = primary
        self.fallback = fallback
        self._dimension = primary.dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return f"{self.primary.model_name}|{self.fallback.model_name}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed with fallback."""
        try:
            return await self.primary.embed(texts)
        except Exception as e:
            logger.warning(
                "primary_embedding_failed",
                primary=self.primary.model_name,
                fallback=self.fallback.model_name,
                error=str(e),
            )
            return await self.fallback.embed(texts)


class CachedEmbeddingProvider:
    """Wraps an :class:`EmbeddingProvider`, consulting an on-disk cache
    before forwarding uncached texts to the inner provider.

    Per-text keying: two overlapping batches share entries. Empty /
    whitespace inputs pass through to the zero-vector contract without
    touching the cache. See
    docs/superpowers/specs/2026-05-14-embedding-cache-design.md.
    """

    def __init__(self, *, inner: Any, cache: Any) -> None:
        self.inner = inner
        self.cache = cache

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    def dimension(self) -> int:
        return self.inner.dimension

    async def embed(
        self,
        texts: list[str],
        cache: bool = True,
    ) -> list[list[float]]:
        if not texts:
            return []

        # Build per-text keys, but only for non-empty texts (matches
        # the inner providers' empty-input contract).
        from perspicacite.llm.embedding_cache import build_embedding_cache_key

        zero = [0.0] * self.inner.dimension
        keys: list[str | None] = []
        for t in texts:
            if not t or not t.strip():
                keys.append(None)
            else:
                keys.append(
                    build_embedding_cache_key(model=self.inner.model_name, text=t)
                )

        # Cache-bypass: straight to inner, no read, no write.
        if not cache:
            # Inner provider already handles empties → zero vec.
            return await self.inner.embed(texts)

        # Batch read.
        non_null_keys = [k for k in keys if k is not None]
        hits = await self.cache.get_many(non_null_keys) if non_null_keys else {}

        # Build the result list, collecting misses to send to inner.
        out: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, (t, k) in enumerate(zip(texts, keys)):
            if k is None:
                out[i] = zero  # empty/whitespace stays zero-vector
            elif k in hits:
                out[i] = hits[k]
            else:
                miss_indices.append(i)
                miss_texts.append(t)

        if miss_texts:
            new_vecs = await self.inner.embed(miss_texts)
            # Write to cache + slot into out in original order.
            put_items: list[tuple[str, str, list[float]]] = []
            for idx, vec in zip(miss_indices, new_vecs):
                out[idx] = vec
                k = keys[idx]
                if k is not None:
                    put_items.append((k, self.inner.model_name, vec))
            if put_items:
                await self.cache.put_many(put_items)

        # Final result — every slot is filled.
        return [v if v is not None else zero for v in out]


def create_embedding_provider(
    model: str,
    use_local_fallback: bool = True,
    *,
    cache_enabled: bool = False,
    cache_path: "Path | str | None" = None,
    cache_ttl_days: int = 0,
) -> EmbeddingProvider:
    """
    Factory function to create an embedding provider.

    Args:
        model: Model name (e.g., 'text-embedding-3-small' or 'all-MiniLM-L6-v2')
        use_local_fallback: Whether to set up local fallback for API providers.
        cache_enabled: When True, wrap the returned provider in a
            :class:`CachedEmbeddingProvider`. The cache key is
            ``sha256(model || \\x00 || text)``, so switching models
            transparently invalidates the cache.
        cache_path: SQLite file backing the cache. Required when
            ``cache_enabled`` is True.
        cache_ttl_days: Days until a cached vector expires. 0 (default) =
            keep forever. Embeddings are deterministic per (model, text),
            so this is safe.

    Returns:
        EmbeddingProvider instance (possibly wrapped in caching).
    """
    # Inner-provider selection (unchanged logic)
    if model.startswith("all-") or "/" not in model and "embedding" not in model:
        inner: EmbeddingProvider = SentenceTransformerEmbeddingProvider(model=model)
    else:
        primary = LiteLLMEmbeddingProvider(model=model)
        if use_local_fallback:
            fallback = SentenceTransformerEmbeddingProvider()
            inner = FallbackEmbeddingProvider(primary, fallback)
        else:
            inner = primary

    if not cache_enabled:
        return inner

    if cache_path is None:
        raise ValueError(
            "create_embedding_provider(cache_enabled=True) requires cache_path"
        )

    # Import lazily to avoid importing numpy unless the cache is used.
    from perspicacite.llm.embedding_cache import EmbeddingCache

    cache = EmbeddingCache(path=cache_path, ttl_days=cache_ttl_days)
    return CachedEmbeddingProvider(inner=inner, cache=cache)
