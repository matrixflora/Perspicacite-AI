#!/usr/bin/env python3
"""End-to-end audit of the 2026-05-15 sub-projects (A/B/C/cite-graph).

Runs against TWO real articles to verify the integrated pipeline:

Article 1: Retrieval-Augmented Generation (Lewis et al. 2020)
  - DOI: 10.48550/arXiv.2005.11401
  - GitHub: huggingface/transformers (we exercise tools/run_clm.py as a
    small Python file inside a real repo for code-aware chunking demos)

Article 2: AlphaFold (Jumper et al. 2021)
  - DOI: 10.1038/s41586-021-03819-2
  - GitHub: deepmind/alphafold (we exercise alphafold/__init__.py for
    AST chunking)

What this audit exercises:
- Sub-project A: code-aware AST chunking on small real files
- Sub-project B: TypedEmbeddingProvider routing (stubbed; no Mistral key)
- Sub-project C: code excerpt + figure ref extraction + SSE event factories
- Cite-graph: live OpenAlex forward-citation walk on each paper's DOI

Findings go to tests/audit/results/2026-05-15-<ts>.md.

Run as:
    python tests/audit/run_2026_05_15_audit.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

# Make repo importable when run as a script.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from perspicacite.config.schema import (
    CiteGraphConfig,
    KnowledgeBaseConfig,
    MultimodalConfig,
    MultimodalMode,
)
from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.models.rag import CodeExcerpt, FigureRef, RAGMode, RAGResponse, StreamEvent
from perspicacite.pipeline.chunking_code import chunk_code, _chunk_python_ast
from perspicacite.pipeline.symbol_index import (
    append_symbols,
    iter_symbols,
    symbols_from_chunks,
    write_chunks_symbols,
)
from perspicacite.pipeline.library_doi import resolve_library_paper
from perspicacite.pipeline.cite_graph import (
    CiteHit,
    apply_cite_graph_filters,
    enrich_kb_from_cite_graph,
    score_cite_hit,
)
from perspicacite.rag.code_excerpts import build_github_source_url, collect_code_excerpts
from perspicacite.rag.figure_refs import collect_figure_refs
from perspicacite.llm.embeddings import TypedEmbeddingProvider


RESULTS_DIR = ROOT / "tests" / "audit" / "results"
RESULTS_DIR.mkdir(exist_ok=True, parents=True)


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

ARTICLES = [
    {
        "id": "rag",
        "name": "Retrieval-Augmented Generation",
        "doi": "10.48550/arXiv.2005.11401",
        "github_repo": "huggingface/transformers",
        "github_sha": "main",  # use "main" branch HEAD
        "code_file_url": (
            "https://raw.githubusercontent.com/huggingface/transformers/"
            "main/src/transformers/models/rag/modeling_rag.py"
        ),
        "code_file_path": "src/transformers/models/rag/modeling_rag.py",
        "associated_library": "transformers",
    },
    {
        "id": "alphafold",
        "name": "AlphaFold protein structure prediction",
        "doi": "10.1038/s41586-021-03819-2",
        "github_repo": "deepmind/alphafold",
        "github_sha": "main",
        "code_file_url": (
            "https://raw.githubusercontent.com/deepmind/alphafold/main/alphafold/common/residue_constants.py"
        ),
        "code_file_path": "alphafold/common/residue_constants.py",
        "associated_library": "alphafold",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_url(url: str, *, timeout: float = 30.0) -> str | None:
    """Best-effort HTTP GET (text). Returns None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"  ! fetch failed for {url}: {exc}")
        return None


class _StubEmbedder:
    """Tiny embedder stub for TypedEmbeddingProvider routing smoke test."""
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
        return [[float(ord(self._name[0])) / 256.0] * self._dim for _ in texts]


# ---------------------------------------------------------------------------
# Per-article audit
# ---------------------------------------------------------------------------

async def audit_article(article: dict[str, Any], findings: dict[str, Any]) -> None:
    print(f"\n=== {article['name']} ({article['id']}) ===")
    art_findings: dict[str, Any] = {"name": article["name"]}
    findings[article["id"]] = art_findings

    # --- A. Code-aware AST chunking on a real file -----------------------
    print(f"  [A] Fetching {article['code_file_url']}")
    t0 = time.perf_counter()
    source = fetch_url(article["code_file_url"])
    fetch_s = round(time.perf_counter() - t0, 3)

    if source is None:
        art_findings["sub_a"] = {"status": "fetch_failed"}
        print(f"  [A] fetch failed; skipping AST analysis")
    else:
        size_kb = round(len(source) / 1024, 1)
        print(f"  [A] fetched {size_kb} KB in {fetch_s}s")

        paper_id = (
            f"github:{article['github_repo']}@{article['github_sha']}"
            f":{article['code_file_path']}"
        )
        paper = Paper(
            id=paper_id, title=article["code_file_path"],
            abstract="", source=PaperSource.BIBTEX,
        )

        t1 = time.perf_counter()
        chunks = _chunk_python_ast(
            source, paper, file_path=article["code_file_path"],
            chunk_size=1000, chunk_overlap=200,
        )
        chunk_s = round(time.perf_counter() - t1, 3)

        kinds = sorted({c.metadata.symbol_kind for c in chunks if c.metadata.symbol_kind})
        with_docstring = sum(1 for c in chunks if c.metadata.docstring)
        imports = chunks[0].metadata.imports if chunks else []

        art_findings["sub_a"] = {
            "status": "ok",
            "fetch_seconds": fetch_s,
            "chunk_seconds": chunk_s,
            "file_size_kb": size_kb,
            "n_chunks": len(chunks),
            "kinds": kinds,
            "chunks_with_docstring": with_docstring,
            "top_3_imports": imports[:3],
            "first_3_symbols": [
                (c.metadata.symbol_name, c.metadata.symbol_kind,
                 f"L{c.metadata.start_line}-L{c.metadata.end_line}")
                for c in chunks[:3]
            ],
        }
        print(f"  [A] {len(chunks)} chunks, kinds={kinds}, "
              f"{with_docstring} w/docstring, {len(imports)} imports")

        # --- Symbol index sidecar ------------------------------------
        # 2026-05-15: write to a per-run tempdir instead of
        # tests/audit/results/audit_kb/ so the audit harness doesn't
        # churn tracked files. The sidecar is consumed read-back below;
        # there's no need to persist it across runs.
        import tempfile
        kb_dir = Path(tempfile.mkdtemp(prefix=f"audit_kb_{article['id']}_"))
        sidecar = kb_dir / "symbols.jsonl"
        n_written = write_chunks_symbols(kb_dir=kb_dir, chunks=chunks)
        n_read = sum(1 for _ in iter_symbols(kb_dir))
        art_findings["symbol_index"] = {
            "written": n_written, "read_back": n_read,
            "sidecar_path": str(sidecar.relative_to(ROOT)),
        }
        print(f"  [A] symbol_index: wrote {n_written}, read back {n_read}")

        # --- C: code excerpt extraction with GitHub URL ----------------
        excerpts = collect_code_excerpts(chunks)
        first_url = excerpts[0].source_url if excerpts else None
        art_findings["sub_c_code_excerpts"] = {
            "n_excerpts": len(excerpts),
            "first_source_url": first_url,
        }
        print(f"  [C] {len(excerpts)} code excerpts, sample URL: {first_url}")

    # --- B. TypedEmbeddingProvider routing (smoke; no live key) ---------
    print(f"  [B] TypedEmbeddingProvider routing smoke test")
    text_stub = _StubEmbedder("text-embedding-3-small", dim=4)
    code_stub = _StubEmbedder("codestral-embed", dim=4)
    tp = TypedEmbeddingProvider(
        default=text_stub, by_content_type={"code": code_stub},
    )
    test_texts = ["the cat sat on the mat", "def fit(): pass", "another text"]
    test_types = ["text", "code", "text"]
    vecs = await tp.embed(test_texts, content_types=test_types)
    routing_correct = (
        len(vecs) == 3
        and text_stub.calls == [["the cat sat on the mat", "another text"]]
        and code_stub.calls == [["def fit(): pass"]]
    )
    art_findings["sub_b"] = {
        "status": "ok" if routing_correct else "routing_broken",
        "model_name": tp.model_name,
        "dimension": tp.dimension,
        "text_batch": text_stub.calls,
        "code_batch": code_stub.calls,
    }
    print(f"  [B] routing correct: {routing_correct}; "
          f"model_name={tp.model_name}")

    # --- C: SSE event factories ----------------------------------------
    print(f"  [C] SSE event factory smoke")
    ev_code = StreamEvent.code_excerpt({
        "id": "x", "language": "python", "text": "def f(): pass",
        "source_url": "https://github.com/x/y/blob/z/f.py#L1-L2",
    })
    ev_fig = StreamEvent.figure_ref({
        "id": "pdf_p1_i1", "paper_id": "p", "label": "Figure 1",
        "caption": "test",
    })
    sse_ok = ev_code.event == "code_excerpt" and ev_fig.event == "figure_ref"
    art_findings["sub_c_sse"] = {"status": "ok" if sse_ok else "broken"}
    print(f"  [C] SSE events: {sse_ok}")

    # --- Cite-graph: real OpenAlex --------------------------------------
    print(f"  [cite-graph] Resolving {article['associated_library']} → "
          f"DOI {article['doi']} → forward-citation walk")
    t2 = time.perf_counter()
    kb_cfg = KnowledgeBaseConfig(
        library_paper_map={article["associated_library"]: article["doi"]},
        cite_graph=CiteGraphConfig(
            max_papers=10,
            min_year_offset=10,  # broad window for the audit
            min_citations=0,     # don't filter out any
        ),
    )
    try:
        hits = await enrich_kb_from_cite_graph(
            doi=article["doi"],
            tool=article["associated_library"],
            kb_config=kb_cfg,
            existing_dois=set(),
            dry_run=True,
        )
        cite_s = round(time.perf_counter() - t2, 2)
        print(f"  [cite-graph] {len(hits)} hits in {cite_s}s")
        art_findings["cite_graph"] = {
            "status": "ok",
            "seconds": cite_s,
            "n_hits": len(hits),
            "top_3": [
                {
                    "doi": h.doi, "year": h.year,
                    "citation_count": h.citation_count,
                    "score": h.score,
                    "title": h.title[:120],
                }
                for h in hits[:3]
            ],
        }
        if hits:
            print(f"      top hit: score={hits[0].score:.3f}  "
                  f"y={hits[0].year}  cit={hits[0].citation_count}  "
                  f"{hits[0].title[:80]}")
    except Exception as exc:
        cite_s = round(time.perf_counter() - t2, 2)
        art_findings["cite_graph"] = {
            "status": "error",
            "seconds": cite_s,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(),
        }
        print(f"  [cite-graph] ERROR after {cite_s}s: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    findings: dict[str, Any] = {
        "timestamp": ts,
        "git_sha": os.popen("git rev-parse --short HEAD").read().strip(),
        "articles": {},
    }

    for article in ARTICLES:
        try:
            await audit_article(article, findings["articles"])
        except Exception as exc:
            print(f"\n  !!! audit_article failed for {article['id']}: {exc}")
            findings["articles"][article["id"]] = {
                "status": "fatal", "error": str(exc),
                "trace": traceback.format_exc(),
            }

    # Persist findings
    out_json = RESULTS_DIR / f"2026-05-15-audit-{ts}.json"
    out_json.write_text(json.dumps(findings, indent=2, default=str))
    print(f"\nJSON: {out_json.relative_to(ROOT)}")

    # Human-readable summary
    md_lines = [
        f"# 2026-05-15 sub-project audit — {ts}",
        "",
        f"Git SHA: `{findings['git_sha']}`",
        "",
        "Two articles exercising sub-A (AST chunking), sub-B (typed embeddings),",
        "sub-C (excerpt + SSE), and cite-graph (live OpenAlex).",
        "",
    ]
    for art_id, art in findings["articles"].items():
        md_lines.append(f"## {art.get('name', art_id)}")
        md_lines.append("")
        for section, payload in art.items():
            if section == "name":
                continue
            md_lines.append(f"### {section}")
            md_lines.append("```json")
            md_lines.append(json.dumps(payload, indent=2, default=str)[:4000])
            md_lines.append("```")
            md_lines.append("")
    out_md = RESULTS_DIR / f"2026-05-15-audit-{ts}.md"
    out_md.write_text("\n".join(md_lines))
    print(f"MD:   {out_md.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
