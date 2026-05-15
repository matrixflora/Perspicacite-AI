# Per-type embedding routing — Implementation Plan (sub-project B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route code chunks through a code-specialised embedder (Mistral `codestral-embed`) while keeping text chunks on the default embedder. No regression when the config map is empty.

**Architecture:** A new `TypedEmbeddingProvider` wraps multiple inner `EmbeddingProvider` instances and dispatches by `content_type`. `LLMConfig.embedding_models_per_type: dict[str, str]` is the user-facing config. `ChunkMetadata.embedding_model: Optional[str]` records which model actually produced each chunk's vector. The factory `create_embedding_provider` gains a per-type variant; the existing entry point is unchanged.

**Tech Stack:** Pydantic v2, litellm (for `mistral/codestral-embed` via `litellm.aembedding`). The Mistral API key (`MISTRAL_API_KEY`) is NOT available at design time — all live-path tests are mocked; live verification deferred per the spec.

**Spec:** `docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` (sub-project B)

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/perspicacite/config/schema.py` | MODIFY | Add `LLMConfig.embedding_models_per_type` |
| `src/perspicacite/models/documents.py` | MODIFY | Add `ChunkMetadata.embedding_model` |
| `src/perspicacite/llm/embeddings.py` | MODIFY | Add `TypedEmbeddingProvider`; extend `create_embedding_provider` |
| `src/perspicacite/rag/dynamic_kb.py` | MODIFY (light) | When per-type map is set, build a `TypedEmbeddingProvider`; pass `content_types` at embed time |
| `config.claude_code.example.yml` | MODIFY | Add a commented-out `embedding_models_per_type` block |
| `tests/unit/test_typed_embedding_provider.py` | CREATE | Routing + empty-map parity + dim mismatch |
| `tests/unit/test_chunk_metadata_embedding_model.py` | CREATE | New field defaults and round-trip |
| `tests/unit/test_embedding_factory_per_type.py` | CREATE | Factory builds correct shape |
| `tests/unit/test_llm_config_per_type_embeddings.py` | CREATE | Config field validation |

---

## Task 1: `LLMConfig.embedding_models_per_type` config field

**Files:**
- Modify: `src/perspicacite/config/schema.py:412+`
- Test: `tests/unit/test_llm_config_per_type_embeddings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm_config_per_type_embeddings.py
from perspicacite.config.schema import LLMConfig


def test_default_is_empty_dict():
    cfg = LLMConfig()
    assert cfg.embedding_models_per_type == {}


def test_accepts_per_type_map():
    cfg = LLMConfig(embedding_models_per_type={
        "code": "mistral/codestral-embed",
        "text": "text-embedding-3-small",
    })
    assert cfg.embedding_models_per_type["code"] == "mistral/codestral-embed"
    assert cfg.embedding_models_per_type["text"] == "text-embedding-3-small"
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/unit/test_llm_config_per_type_embeddings.py -v
```

Expected: FAIL — no `embedding_models_per_type` attribute.

- [ ] **Step 3: Add the field**

In `src/perspicacite/config/schema.py`, inside `class LLMConfig(BaseModel)` (around line 412), add the field below `default_model` (near the top of the class body, before `max_context_window`):

```python
    embedding_models_per_type: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional per-content-type embedding model routing. "
            "Keys are content types ('code', 'text', 'markdown', etc.); "
            "values are model strings passed to the embedding factory. "
            "When empty (default), every chunk goes through a single "
            "embedder selected from KnowledgeBaseConfig.embedding_model. "
            "Example: {'code': 'mistral/codestral-embed', 'text': "
            "'text-embedding-3-small'}."
        ),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```
pytest tests/unit/test_llm_config_per_type_embeddings.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_llm_config_per_type_embeddings.py src/perspicacite/config/schema.py
git commit -m "feat(config): LLMConfig.embedding_models_per_type for per-type embedding routing"
```

---

## Task 2: `ChunkMetadata.embedding_model` field

**Files:**
- Modify: `src/perspicacite/models/documents.py`
- Test: `tests/unit/test_chunk_metadata_embedding_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunk_metadata_embedding_model.py
from perspicacite.models.documents import ChunkMetadata


def test_embedding_model_defaults_to_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.embedding_model is None


def test_embedding_model_round_trip():
    md = ChunkMetadata(
        paper_id="p", chunk_index=0,
        embedding_model="mistral/codestral-embed",
    )
    assert md.embedding_model == "mistral/codestral-embed"
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/unit/test_chunk_metadata_embedding_model.py -v
```

Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add the field**

Append to `class ChunkMetadata(BaseModel)` in `src/perspicacite/models/documents.py` (alongside the sub-project A code-aware fields):

```python
    # Sub-project B (per-type embedding routing) — records which embedder
    # actually produced the chunk's vector. None when not yet embedded.
    embedding_model: Optional[str] = None
```

- [ ] **Step 4: Run the test to verify it passes**

```
pytest tests/unit/test_chunk_metadata_embedding_model.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunk_metadata_embedding_model.py src/perspicacite/models/documents.py
git commit -m "feat(models): ChunkMetadata.embedding_model (sub-project B)"
```

---

## Task 3: `TypedEmbeddingProvider` class

**Files:**
- Modify: `src/perspicacite/llm/embeddings.py`
- Test: `tests/unit/test_typed_embedding_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_typed_embedding_provider.py
from __future__ import annotations

import pytest

from perspicacite.llm.embeddings import TypedEmbeddingProvider


class _StubProvider:
    def __init__(self, name: str, dim: int = 4):
        self._name = name
        self._dim = dim
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Vectors are stamped with the provider name's first char for visibility.
        marker = float(ord(self._name[0]) if self._name else 0)
        return [[marker] * self._dim for _ in texts]


@pytest.mark.asyncio
async def test_routes_by_content_type_preserving_order():
    code = _StubProvider("codestral-embed")
    text = _StubProvider("text-embedding-3-small")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})

    texts = ["def f(): pass", "the cat sat", "another snippet"]
    types = ["code", "text", "code"]
    vecs = await tp.embed(texts, content_types=types)

    # Output order matches input order.
    assert len(vecs) == 3
    # 'c' for codestral, 't' for text.
    assert vecs[0][0] == float(ord("c"))
    assert vecs[1][0] == float(ord("t"))
    assert vecs[2][0] == float(ord("c"))

    # Each provider got exactly its share, contiguous.
    assert code.calls == [["def f(): pass", "another snippet"]]
    assert text.calls == [["the cat sat"]]


@pytest.mark.asyncio
async def test_missing_type_falls_through_to_default():
    text = _StubProvider("text-embedding-3-small")
    tp = TypedEmbeddingProvider(default=text, by_content_type={})

    vecs = await tp.embed(["hi", "yo"], content_types=["markdown", "pdf"])
    assert len(vecs) == 2
    assert text.calls == [["hi", "yo"]]


@pytest.mark.asyncio
async def test_none_content_types_uses_default_only():
    text = _StubProvider("text-embedding-3-small")
    code = _StubProvider("codestral-embed")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})

    vecs = await tp.embed(["a", "b"])  # no content_types kwarg
    assert len(vecs) == 2
    assert text.calls == [["a", "b"]]
    assert code.calls == []


def test_model_name_composes_inner_names():
    text = _StubProvider("text-embedding-3-small")
    code = _StubProvider("codestral-embed")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    # Stable composition; default first, then sorted-key inner providers.
    assert tp.model_name == "text-embedding-3-small+code:codestral-embed"


def test_dimension_matches_default_when_homogeneous():
    text = _StubProvider("text-embedding-3-small", dim=8)
    code = _StubProvider("codestral-embed", dim=8)
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    assert tp.dimension == 8


def test_dimension_none_when_inner_dims_disagree():
    text = _StubProvider("text-embedding-3-small", dim=8)
    code = _StubProvider("codestral-embed", dim=4)
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    assert tp.dimension is None
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/unit/test_typed_embedding_provider.py -v
```

Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `TypedEmbeddingProvider`**

Append to `src/perspicacite/llm/embeddings.py` (after `FallbackEmbeddingProvider`):

```python
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
        provider_for: list[EmbeddingProvider] = []
        for i, (t, ctype) in enumerate(zip(texts, content_types)):
            prov = self._by_type.get(ctype, self._default)
            provider_for.append(prov)
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

        # All slots must be filled.
        if any(v is None for v in out):
            raise RuntimeError("internal: TypedEmbeddingProvider left a None slot")
        return out  # type: ignore[return-value]
```

- [ ] **Step 4: Run the test to verify it passes**

```
pytest tests/unit/test_typed_embedding_provider.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_typed_embedding_provider.py src/perspicacite/llm/embeddings.py
git commit -m "feat(llm): TypedEmbeddingProvider routes embed by content_type (sub-project B)"
```

---

## Task 4: Factory variant for per-type routing

**Files:**
- Modify: `src/perspicacite/llm/embeddings.py` (`create_embedding_provider`)
- Test: `tests/unit/test_embedding_factory_per_type.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_embedding_factory_per_type.py
from __future__ import annotations

import pytest

from perspicacite.llm.embeddings import (
    SentenceTransformerEmbeddingProvider,
    TypedEmbeddingProvider,
    create_embedding_provider,
)


def test_empty_per_type_returns_single_provider():
    """When the map is empty, factory behaves identically to today."""
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={},  # explicit empty map
    )
    assert isinstance(prov, SentenceTransformerEmbeddingProvider)


def test_per_type_map_returns_typed_provider():
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={"code": "all-MiniLM-L12-v2"},
    )
    assert isinstance(prov, TypedEmbeddingProvider)
    # Default model is the top-level `model`; "code" is overridden.
    assert "all-MiniLM-L6-v2" in prov.model_name
    assert "code:all-MiniLM-L12-v2" in prov.model_name


def test_per_type_default_already_referenced_dedups():
    """If the per-type map sets 'text' to the same model as `model`,
    we still build a TypedEmbeddingProvider (it's the routing trigger),
    but the inner-provider list is sane (one provider per type, default
    handles unspecified types)."""
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={"text": "all-MiniLM-L6-v2"},
    )
    assert isinstance(prov, TypedEmbeddingProvider)
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/unit/test_embedding_factory_per_type.py -v
```

Expected: FAIL — `create_embedding_provider` doesn't accept `embedding_models_per_type`.

- [ ] **Step 3: Extend the factory**

In `src/perspicacite/llm/embeddings.py`, replace the `create_embedding_provider` signature and body to accept `embedding_models_per_type`. The existing body keeps its inner-provider selection; the new branch builds a `TypedEmbeddingProvider` over it.

```python
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

    See the existing docstring above. New optional kwarg:

    Args:
        embedding_models_per_type: Optional content-type → model string
            map. When non-empty, the returned provider is a
            :class:`TypedEmbeddingProvider` that dispatches by
            ``content_type`` and uses ``model`` as its default.

    Returns:
        EmbeddingProvider (or TypedEmbeddingProvider when per-type
        routing is configured). Caching wraps the outer object so the
        cache key reflects the routed model.
    """
    def _build_single(m: str) -> EmbeddingProvider:
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

    from perspicacite.llm.embedding_cache import EmbeddingCache

    cache = EmbeddingCache(path=cache_path, ttl_days=cache_ttl_days)
    return CachedEmbeddingProvider(inner=inner, cache=cache)
```

- [ ] **Step 4: Run the new tests AND the existing factory tests to confirm no regression**

```
pytest tests/unit/test_embedding_factory_per_type.py tests/unit/test_embedding_fallback_tracking.py -v 2>&1 | tail -25
```

Expected: new 3 pass; existing fallback tests stay green.

If there's a `test_embeddings.py` or similar pre-existing test, run that too:

```
pytest tests/unit/test_embeddings.py tests/unit/test_embedding_cache.py -v 2>&1 | tail -10 || echo "no preexisting tests"
```

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_embedding_factory_per_type.py src/perspicacite/llm/embeddings.py
git commit -m "feat(llm): create_embedding_provider accepts embedding_models_per_type"
```

---

## Task 5: Stamp `embedding_model` on chunks at ingest

**Files:**
- Modify: `src/perspicacite/rag/dynamic_kb.py` (or wherever embeddings are produced from chunks during ingest — search first)
- Test: `tests/unit/test_chunk_embedding_model_stamp.py`

- [ ] **Step 1: Find the embed-at-ingest site**

Use `grep` to locate the function that takes `chunks` and produces embeddings during ingest:

```
cd /Users/holobiomicslab/git/Perspicacite-AI
grep -rEn "\\.embed\\(|embedder\\.embed" src/perspicacite/rag/ src/perspicacite/pipeline/ 2>/dev/null | grep -v __pycache__ | head -20
```

Note the file:line(s) where chunks are embedded. The most likely location is `src/perspicacite/rag/dynamic_kb.py` in a method that loops chunks and calls `embedder.embed(...)`.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_chunk_embedding_model_stamp.py
"""When chunks are embedded during ingest, each chunk's metadata
``embedding_model`` is updated to the actual model used."""
from __future__ import annotations

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.llm.embeddings import TypedEmbeddingProvider


class _Stub:
    def __init__(self, name: str, dim: int = 4):
        self._name = name
        self._dim = dim

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


def _chunk(ctype: str, paper_id: str = "p", idx: int = 0) -> DocumentChunk:
    return DocumentChunk(
        id=f"{paper_id}_{idx}",
        text=f"sample {ctype} text",
        metadata=ChunkMetadata(paper_id=paper_id, chunk_index=idx, content_type=ctype),
    )


@pytest.mark.asyncio
async def test_stamps_routed_model_per_chunk():
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    typed = TypedEmbeddingProvider(
        default=_Stub("text-embedding-3-small"),
        by_content_type={"code": _Stub("mistral/codestral-embed")},
    )
    chunks = [_chunk("text", "p1", 0), _chunk("code", "p1", 1)]
    out = stamp_embedding_models_on_chunks(chunks, embedder=typed)
    assert out[0].metadata.embedding_model == "text-embedding-3-small"
    assert out[1].metadata.embedding_model == "mistral/codestral-embed"


@pytest.mark.asyncio
async def test_stamps_single_model_when_provider_is_not_typed():
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    single = _Stub("text-embedding-3-small")
    chunks = [_chunk("text", "p1", 0), _chunk("code", "p1", 1)]
    out = stamp_embedding_models_on_chunks(chunks, embedder=single)
    assert all(c.metadata.embedding_model == "text-embedding-3-small" for c in out)
```

- [ ] **Step 3: Run the test to verify it fails**

```
pytest tests/unit/test_chunk_embedding_model_stamp.py -v
```

Expected: FAIL — `stamp_embedding_models_on_chunks` doesn't exist.

- [ ] **Step 4: Add the helper in `dynamic_kb.py`**

Find an appropriate location near the top-level of `src/perspicacite/rag/dynamic_kb.py` (above the main ingest function). Add:

```python
def stamp_embedding_models_on_chunks(
    chunks: list[DocumentChunk],
    *,
    embedder,  # EmbeddingProvider or TypedEmbeddingProvider — kept untyped to avoid circular import
) -> list[DocumentChunk]:
    """Return new chunks with ``metadata.embedding_model`` set to the
    actual model that would handle each chunk's content type.

    For a :class:`TypedEmbeddingProvider`, this reads the routing map.
    For any other provider, every chunk gets ``embedder.model_name``.

    The original chunks are NOT mutated (ChunkMetadata is frozen);
    new chunks are returned via ``model_copy``.
    """
    from perspicacite.llm.embeddings import TypedEmbeddingProvider

    if isinstance(embedder, TypedEmbeddingProvider):
        by_type = embedder._by_type            # type: ignore[attr-defined]
        default = embedder._default            # type: ignore[attr-defined]
        def _model_for(ctype: str | None) -> str:
            if ctype and ctype in by_type:
                return by_type[ctype].model_name
            return default.model_name
    else:
        single_name = embedder.model_name
        def _model_for(ctype: str | None) -> str:
            del ctype
            return single_name

    out: list[DocumentChunk] = []
    for c in chunks:
        md_updated = c.metadata.model_copy(
            update={"embedding_model": _model_for(c.metadata.content_type)}
        )
        out.append(DocumentChunk(id=c.id, text=c.text, metadata=md_updated))
    return out
```

If `DocumentChunk` is not already imported at the top of `dynamic_kb.py`, add:

```python
from perspicacite.models.documents import DocumentChunk
```

(Skip the add if already imported.)

- [ ] **Step 5: Run the test to verify it passes**

```
pytest tests/unit/test_chunk_embedding_model_stamp.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Wire the stamper into the ingest path**

In the same file `dynamic_kb.py`, find the function that produces chunks and calls `embedder.embed(...)` (located in step 1). Right before the embed call OR right after chunks are produced (before they're persisted), call the new helper:

```python
chunks = stamp_embedding_models_on_chunks(chunks, embedder=self._embedder)
```

(Use whichever variable name holds the embedder in that scope. If the embedder is built from `create_embedding_provider(...)` with the per-type kwarg, this stamping will see a `TypedEmbeddingProvider` and route correctly.)

Also: when the call to `embedder.embed(...)` is reachable AND the embedder is a `TypedEmbeddingProvider`, pass `content_types` so it routes:

```python
from perspicacite.llm.embeddings import TypedEmbeddingProvider
if isinstance(self._embedder, TypedEmbeddingProvider):
    content_types = [c.metadata.content_type or "text" for c in chunks]
    embeddings = await self._embedder.embed([c.text for c in chunks], content_types=content_types)
else:
    embeddings = await self._embedder.embed([c.text for c in chunks])
```

If the existing embed call is inside a different abstraction (e.g. a Chroma adapter), apply the same shape there.

- [ ] **Step 7: Run regression**

```
pytest tests/unit/test_chunk_embedding_model_stamp.py tests/unit/test_typed_embedding_provider.py tests/unit/test_embedding_factory_per_type.py tests/unit/test_chunk_metadata_embedding_model.py tests/unit/test_llm_config_per_type_embeddings.py -v
```

Expected: all green.

Then a broader regression on the RAG/embedding surface:

```
pytest tests/unit/test_embedding_fallback_tracking.py tests/unit/test_embedding_cache.py 2>&1 | tail -10 || echo "tests skipped/missing"
```

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_chunk_embedding_model_stamp.py src/perspicacite/rag/dynamic_kb.py
git commit -m "feat(rag): stamp ChunkMetadata.embedding_model from routed provider at ingest"
```

---

## Task 6: Example config update

**Files:**
- Modify: `config.claude_code.example.yml`

- [ ] **Step 1: Inspect the existing file**

```
grep -n "embedding\|llm:" /Users/holobiomicslab/git/Perspicacite-AI/config.claude_code.example.yml | head -20
```

Locate the `llm:` block.

- [ ] **Step 2: Add a commented example**

Append inside the `llm:` block (or wherever the LLM config lives — adjust as appropriate to the file's structure):

```yaml
  # Optional: route different content types through different embedders.
  # When this block is omitted or empty, every chunk uses the single
  # KnowledgeBaseConfig.embedding_model (today's behaviour). When set,
  # the named keys override the default for chunks whose
  # ChunkMetadata.content_type matches.
  #
  # The example below uses Mistral's codestral-embed for code and a
  # smaller OpenAI text model for everything else. Requires a Mistral
  # API key (MISTRAL_API_KEY env var); when missing, the call falls
  # back to the default embedder with a structured warning.
  #
  # embedding_models_per_type:
  #   code: "mistral/codestral-embed"
  #   text: "text-embedding-3-small"
```

- [ ] **Step 3: Sanity-check YAML parse**

```
python3 -c "import yaml; yaml.safe_load(open('config.claude_code.example.yml')); print('yaml parse ok')"
```

Expected: `yaml parse ok`.

- [ ] **Step 4: Commit**

```bash
git add config.claude_code.example.yml
git commit -m "docs(config): commented embedding_models_per_type example with codestral-embed"
```

---

## Self-Review

**Spec coverage** (`docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` §4):

| Spec section | Task |
|---|---|
| §4.1 Goal — per-type routing | Tasks 3, 4, 5 |
| §4.2 Non-goal — no historical re-embed | Honoured (no re-embed code) |
| §4.3 Default `codestral-embed` | Task 6 (example yml) |
| §4.4 Files | Matches File Map table |
| §4.5 `LLMConfig.embedding_models_per_type` | Task 1 |
| §4.5 `TypedEmbeddingProvider` shape | Task 3 |
| §4.6 `ChunkMetadata.embedding_model` | Task 2 |
| §4.7 Query-time routing | **Out of v1 scope** — flagged below |
| §4.8 Tests | Tasks 1–5 |

**v1 scope limitation:** Query-time per-model routing (§4.7) is intentionally NOT implemented in this plan. For v1, KBs that mix per-type models will query via the default embedder. This is documented as a known limitation; a follow-up plan can add Chroma-side per-model partitioning + RRF query-time fusion. Justification: the user has no Mistral API key today; live verification is deferred; query-time routing complicates ingestion and Chroma collection management without immediate user value. The plumbing (per-chunk `embedding_model` field) is in place so the follow-up can land cleanly when needed.

**Placeholder scan:** All steps have concrete code or commands. No "TBD" / "TODO" / "implement appropriate error handling".

**Type consistency:**
- `TypedEmbeddingProvider(*, default, by_content_type)` signature used identically in Tasks 3, 4, 5.
- `embed(texts, *, content_types=None)` signature consistent.
- `ChunkMetadata.embedding_model: Optional[str] = None` defined in Task 2, used in Task 5 (`md.embedding_model`).
- `create_embedding_provider(..., embedding_models_per_type=None)` signature consistent between Task 4 definition and any future caller.
- `stamp_embedding_models_on_chunks(chunks, *, embedder)` signature consistent between Task 5 step 4 (impl) and step 6 (wiring).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-per-type-embeddings.md`.

After this plan ships, sub-project C and the cite-graph plan follow. The user has already approved subagent-driven execution for the broader 2026-05-15 work; continue in that mode.
