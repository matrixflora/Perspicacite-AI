"""Embedding providers for vector search."""

from pathlib import Path
from typing import Any, Protocol

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
            loop = asyncio.get_running_loop()
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
        # F8 (audit 2026-05-15): track which inner provider actually
        # served the latest embed() call. KB-metadata writers should
        # use ``last_used_model`` rather than the legacy "A|B" tag —
        # vectors from the primary path are dimension/topology-
        # incompatible with vectors from the fallback, so recording
        # both as a single string is a latent footgun.
        self._last_used_model: str = primary.model_name
        self._fallback_triggered_count: int = 0

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        """Return the primary model name only.

        Historically returned ``"primary|fallback"`` for diagnostic
        purposes, but that string was being persisted as the KB's
        embedding fingerprint and broke multi-KB compatibility checks
        against KBs created from the MCP path (which uses
        ``LiteLLMEmbeddingProvider`` directly and so stamps the bare
        primary name). The composite is still available via
        :attr:`composite_name`. Vectors stored in a KB come from the
        primary unless ``primary_embedding_failed`` actually fires, in
        which case ``last_used_model`` records the truth.
        """
        return self.primary.model_name

    @property
    def composite_name(self) -> str:
        """Legacy ``"primary|fallback"`` string — diagnostic use only."""
        return f"{self.primary.model_name}|{self.fallback.model_name}"

    @property
    def last_used_model(self) -> str:
        """F8: actual model that served the most-recent embed() call.

        Use this for KB-metadata storage. ``model_name`` keeps the
        legacy "A|B" syntax for back-compat with existing rows.
        """
        return self._last_used_model

    @property
    def fallback_triggered_count(self) -> int:
        return self._fallback_triggered_count

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed with fallback."""
        try:
            out = await self.primary.embed(texts)
            self._last_used_model = self.primary.model_name
            return out
        except Exception as e:
            self._fallback_triggered_count += 1
            logger.warning(
                "primary_embedding_failed",
                primary=self.primary.model_name,
                fallback=self.fallback.model_name,
                error=str(e),
                fallback_triggered_count=self._fallback_triggered_count,
            )
            out = await self.fallback.embed(texts)
            self._last_used_model = self.fallback.model_name
            return out


class TypedEmbeddingProvider:
    """Routes ``embed`` calls to different inner providers by content type.

    Sub-project B (2026-05-15 design). When the caller passes a parallel
    ``content_types`` list, the texts are partitioned per-type and each
    partition is routed through the matching inner provider (or the
    ``default`` when no specific provider is registered for that type).
    Vectors are stitched back into the original input order.

    When ``content_types`` is ``None`` the call collapses to a single
    invocation of the default provider — identical to today's behaviour
    for callers that don't know about per-type routing.

    Cost shape: at most ``1 + len(by_content_type)`` underlying API
    calls per ``embed`` invocation (one per inner provider that has
    any texts assigned to it).
    """

    def __init__(
        self,
        *,
        default: EmbeddingProvider,
        by_content_type: dict[str, EmbeddingProvider],
    ) -> None:
        self._default = default
        self._by_type = dict(by_content_type)

    @property
    def model_name(self) -> str:
        # Default first, then sorted-key inner providers as "type:model".
        suffix = "+".join(
            f"{k}:{self._by_type[k].model_name}"
            for k in sorted(self._by_type)
        )
        return self._default.model_name + (f"+{suffix}" if suffix else "")

    @property
    def dimension(self) -> int | None:
        """Returns the common dimension when all inner providers agree,
        else None so callers know they must split per-type at write time."""
        dims = {self._default.dimension}
        for p in self._by_type.values():
            dims.add(p.dimension)
        if len(dims) == 1:
            return next(iter(dims))
        return None

    async def embed(
        self,
        texts: list[str],
        *,
        content_types: list[str] | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if content_types is None:
            return await self._default.embed(texts)
        if len(content_types) != len(texts):
            raise ValueError(
                "content_types length must match texts length: "
                f"got {len(content_types)} vs {len(texts)}"
            )

        # Partition input by routed provider; preserve original index.
        buckets: dict[int, tuple[EmbeddingProvider, list[int], list[str]]] = {}
        for i, (t, ctype) in enumerate(zip(texts, content_types)):
            prov = self._by_type.get(ctype, self._default)
            key = id(prov)
            if key not in buckets:
                buckets[key] = (prov, [], [])
            buckets[key][1].append(i)
            buckets[key][2].append(t)

        # Run each bucket through its provider.
        out: list[list[float] | None] = [None] * len(texts)
        for prov, indices, batch_texts in buckets.values():
            vecs = await prov.embed(batch_texts)
            for idx, v in zip(indices, vecs):
                out[idx] = v

        if any(v is None for v in out):
            raise RuntimeError("internal: TypedEmbeddingProvider left a None slot")
        return out  # type: ignore[return-value]


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
    embedding_models_per_type: dict[str, str] | None = None,
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
        embedding_models_per_type: Optional content-type → model string
            map (sub-project B). When non-empty, the returned provider
            is a :class:`TypedEmbeddingProvider` that dispatches by
            ``content_type`` and uses ``model`` as its default. When
            empty or None, behaviour is identical to today (single
            provider).

    Returns:
        EmbeddingProvider (or TypedEmbeddingProvider when per-type
        routing is configured). Caching wraps the outer object so the
        cache key reflects the routed model.
    """
    def _build_single(m: str) -> EmbeddingProvider:
        # Inner-provider selection rule retained from the legacy factory:
        # sentence-transformers for local model names ("all-...") or
        # bare names without a slash/embedding marker; otherwise
        # LiteLLM-backed with optional local fallback.
        if m.startswith("all-") or ("/" not in m and "embedding" not in m):
            return SentenceTransformerEmbeddingProvider(model=m)
        primary = LiteLLMEmbeddingProvider(model=m)
        if use_local_fallback:
            return FallbackEmbeddingProvider(primary, SentenceTransformerEmbeddingProvider())
        return primary

    inner: EmbeddingProvider = _build_single(model)

    if embedding_models_per_type:
        by_type: dict[str, EmbeddingProvider] = {}
        for ctype, ctype_model in embedding_models_per_type.items():
            by_type[ctype] = _build_single(ctype_model)
        inner = TypedEmbeddingProvider(default=inner, by_content_type=by_type)

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
