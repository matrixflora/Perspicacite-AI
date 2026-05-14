# Embedding cache — status & operator guide (2026-05-14)

Wave 2.2 of the framework-hardening roadmap. Disk-cached embedding
vectors for every `EmbeddingProvider`.

## What it does

`CachedEmbeddingProvider` wraps an inner provider (SentenceTransformer
or LiteLLM) and consults `data/embedding_cache.db` per text. On a
hit, the vector returns in <5 ms instead of re-running the encoder /
re-calling the API. Per-text keying means two overlapping batches
share entries — re-ingesting a paper with one new chunk only embeds
that one chunk.

## Why it pays off

- Multi-paper ingest: the dominant cost is the encoder. Identical
  chunks (introduction boilerplate, citation lines, etc.) across
  papers re-hit cache.
- Contextual retrieval iteration: re-running with a different
  retrieval tier reuses every embedding.
- Switching from LiteLLM to local fallback (or vice-versa) without
  re-embedding the bulk: the cache key embeds the model name, so
  switching invalidates correctly but only for the swapped paths.

## Configuration

```yaml
kb:
  embedding_model: text-embedding-3-small
  embedding_cache_enabled: true                 # default true
  embedding_cache_path: data/embedding_cache.db # default
  embedding_cache_ttl_days: 0                   # default forever
```

## Bypass

```python
await provider.embed(texts, cache=False)
```

Used by tests that want to exercise the inner provider directly.

## Clearing

```bash
rm data/embedding_cache.db data/embedding_cache.db-shm data/embedding_cache.db-wal
```

Selective by model:

```bash
sqlite3 data/embedding_cache.db \
  "DELETE FROM embedding_cache WHERE model = 'text-embedding-3-small';"
```

## Storage footprint

float32 × dim ≈ 4·dim bytes per vector.

| Model | Dim | Per vector | 100k chunks |
|---|---|---|---|
| all-MiniLM-L6-v2 | 384 | 1.5 KB | 150 MB |
| all-mpnet-base-v2 | 768 | 3.0 KB | 300 MB |
| text-embedding-3-small | 1536 | 6.0 KB | 600 MB |
| text-embedding-3-large | 3072 | 12.0 KB | 1.2 GB |

Acceptable up to ~1 GB. Beyond that, revisit compression (zstd) or
LRU bounds.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/embedding_cache.py` | `EmbeddingCache` + key builder |
| `src/perspicacite/llm/embeddings.py` | `CachedEmbeddingProvider` + factory wiring |
| `src/perspicacite/config/schema.py` | Three new fields on `KnowledgeBaseConfig` |
| `tests/unit/test_embedding_cache_*.py` | Storage, key, wrapper tests |

## Open followups

- Wire `create_embedding_provider` callsites (mcp/server.py, rag/engine.py,
  retrieval/chroma_store.py) to pass cache config from `LLMConfig`.
  This plan ships the building blocks; callsite migration is a
  follow-up commit once we audit each entry point.
- Compression (zstd) — only if footprint > 1 GB.
- LRU bound — only if footprint > 1 GB.
- Query-time embedding caching — low value, separate decision.
