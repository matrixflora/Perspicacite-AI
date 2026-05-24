"""Round-3 audit — 2026-05-24.

Full regression re-run of round-2 cases + new-feature coverage:
  SMOKE: model smoke test (deepseek-v4-pro)
  ---- Regression (round-2 re-runs) ----
  R-1:  search_literature relevance tiers
  R-2:  DOI batch ingest (content_type, attempts, outcome split)
  R-4:  BibTeX with mixed valid/invalid entries
  R-5:  URL batch with arXiv URL
  R-6:  Multi-KB query
  R-7:  deep_research mode (renamed from profound; F-17 verification)
  R-12: Recency weighting on diverse-year corpus
  R-13: Cite-graph backward direction (F-29 fixed)
  R-14: Conversation FTS5 search
  R-15: KB export to Obsidian zip
  R-16: get_paper_content
  R-17: KB metadata round trip
  R-18: search_knowledge_base direct
  ---- New features (since round 2, 2026-05-23) ----
  N-1:  Claim graph build + status
  N-2:  Query claim graph (papers_with_claim_pattern)
  N-3:  Get claim links
  N-4:  Claim graph export (N-Quads format)
  N-5:  generate_report iteration_count + completion_reason metadata
  N-6:  Early-return diagnostic dict
  N-7:  Literature survey seed filter (seeds kept even if in KB)
  N-8:  F-30 fix: attempts trail on abstract-only successes
  N-9:  F-28 fix: full_text/metadata_only/failed outcome split on DOI ingest
  N-10: F-29 fix: backward cite-graph for arXiv seeds works
"""
import asyncio
import json
import time
from pathlib import Path

import httpx
from fastmcp import Client

BASE = "http://localhost:8000"
MCP_URL = f"{BASE}/mcp"
OUT = Path("/Users/holobiomicslab/git/Perspicacite-AI/audit_2026-05-24")
OUT.mkdir(exist_ok=True)


def _extract(r):
    if hasattr(r, "data") and r.data is not None:
        payload = r.data
    elif hasattr(r, "content") and r.content:
        text = getattr(r.content[0], "text", str(r.content[0]))
        try:
            payload = json.loads(text)
        except Exception:
            payload = text
    else:
        payload = {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {"raw_text": payload[:2000]}
    return payload or {}


async def _cleanup_kb(http_client, name):
    try:
        await http_client.delete(f"{BASE}/api/kb/{name}")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────────────────────────────────────

async def case_smoke(client):
    """Verify deepseek-v4-pro is the active model and the MCP server responds."""
    print("\n=== SMOKE: model + MCP connectivity ===")
    health = (await httpx.AsyncClient().get(f"{BASE}/api/health")).json()
    model = health.get("llm", {}).get("default_model", "?")
    print(f"  Server model: {model}")

    r = await client.call_tool("search_literature", {"query": "what is StructureMASST", "max_results": 2})
    d = _extract(r)
    out = {
        "server_model": model,
        "mcp_ok": d.get("success") is True,
        "error": d.get("error"),
    }
    print(f"  MCP ok={out['mcp_ok']}, error={out.get('error')}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# REGRESSION CASES
# ──────────────────────────────────────────────────────────────────────────────

async def case_R1_search_screening(client):
    """search_literature — relevance tiers, dedup, errors_by_database."""
    print("\n=== R-1: search_literature relevance tiers ===")
    r = await client.call_tool("search_literature", {
        "query": "LLM prompt optimization with evolutionary algorithms",
        "max_results": 20,
        "min_relevance": 0.3,
    })
    d = _extract(r)
    papers = d.get("papers") or []
    scores = [p.get("relevance_score") for p in papers if p.get("relevance_score") is not None]
    has_dups = len(papers) != len({p.get("doi") or p.get("title") for p in papers})
    out = {
        "success": d.get("success"),
        "total": len(papers),
        "errors_by_database": d.get("errors_by_database"),
        "top_scores": scores[:5],
        "has_duplicates": has_dups,
    }
    print(f"  papers={len(papers)}, top_scores={scores[:5]}, has_dups={has_dups}")
    print(f"  errors_by_database: {d.get('errors_by_database')}")
    return out


async def case_R2_doi_ingest(client):
    """DOI batch ingest — content_type, attempts, outcome split (F-28)."""
    print("\n=== R-2: DOI batch ingest ===")
    kb = "r3-r2-doi"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "round-3 regression"})
        dois = [
            "10.48550/arxiv.2309.08532",   # arXiv EvoPrompt (should get full text)
            "10.1007/978-3-031-48316-5_7",  # Springer chapter (likely abstract-only)
            "10.1016/j.cell.2021.03.001",   # Elsevier Cell (likely abstract-only)
        ]
        t0 = time.monotonic()
        resp = await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": dois})
        elapsed = time.monotonic() - t0
        data = resp.json()

    out = {
        "added_papers": data.get("added_papers"),
        "added_with_full_text": data.get("added_with_full_text"),
        "added_metadata_only": data.get("added_metadata_only"),
        "failed": data.get("failed", []),
        "metadata_only": data.get("metadata_only", [])[:3],
        "stats": data.get("stats"),
        "elapsed_s": round(elapsed, 1),
    }
    print(f"  added={out['added_papers']}, full_text={out['added_with_full_text']}, meta_only={out['added_metadata_only']}")
    print(f"  failed={[f.get('doi') or f.get('key') for f in out['failed']]}")
    print(f"  meta_only entries={len(data.get('metadata_only', []))}")
    return out


async def case_R4_bibtex_mixed(client):
    """BibTeX ingest with mixed valid/invalid/no-DOI entries."""
    print("\n=== R-4: BibTeX mixed entries ===")
    kb = "r3-r4-bib"
    bibtex = """
@article{evoPrompt,
  title={EvoPrompt: Language Model Alignment via Evolutionary Strategies},
  author={Guo, Qingyan and Wang, Rui},
  year={2023},
  doi={10.48550/arxiv.2309.08532},
}
@article{selfrag,
  title={Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection},
  author={Asai, Akari},
  year={2023},
  doi={10.48550/arxiv.2310.11511},
}
@article{nodoi,
  title={A Paper Without DOI},
  author={Anonymous Author},
  year={2022},
}
@article{baddoi,
  title={Paper With Garbage DOI},
  author={Unknown},
  year={2021},
  doi={10.99999/totally.fake.xyz.999},
}
@misc{notitle,
  author={Nobody},
  year={2020},
}
"""
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "round-3 bibtex"})
        resp = await http.post(f"{BASE}/api/kb/{kb}/bibtex", content=bibtex, headers={"Content-Type": "text/plain"})
        data = resp.json()

    out = {
        "total_entries": data.get("total_entries"),
        "added_papers": data.get("added_papers"),
        "added_with_full_text": data.get("added_with_full_text"),
        "added_metadata_only": data.get("added_metadata_only"),
        "failed": data.get("failed", []),
        "metadata_only": data.get("metadata_only", []),
    }
    print(f"  total={out['total_entries']}, added={out['added_papers']}, full={out['added_with_full_text']}, meta={out['added_metadata_only']}")
    print(f"  failed: {[f.get('key') or f.get('doi') for f in out['failed']]}")
    return out


async def case_R5_url_batch(client):
    """URL batch ingest — arXiv URL routing, year extraction."""
    print("\n=== R-5: URL batch ingest ===")
    kb = "r3-r5-urls"
    urls = [
        "https://arxiv.org/abs/2309.08532",          # arXiv → should get full structured text
        "https://github.com/huggingface/smolagents", # GitHub → markdown
    ]
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "url batch"})
        resp = await http.post(f"{BASE}/api/kb/{kb}/urls", json={"urls": urls})
        data = resp.json()

    papers = data.get("papers") or []
    out = {
        "total": len(papers),
        "papers": [{"url": p.get("url") or p.get("source_url"), "ct": p.get("content_type"), "chars": p.get("chars")} for p in papers],
        "stats": data.get("stats"),
    }
    print(f"  ingested {len(papers)} URLs")
    for p in out["papers"]:
        print(f"    url={str(p.get('url','?'))[:60]}, ct={p.get('ct')}, chars={p.get('chars')}")
    return out


async def case_R6_multi_kb(client):
    """Multi-KB query consistency across two KBs."""
    print("\n=== R-6: Multi-KB query ===")
    kb_a, kb_b = "r3-r6-a", "r3-r6-b"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb_a)
        await _cleanup_kb(http, kb_b)
        await client.call_tool("create_knowledge_base", {"name": kb_a, "description": "multi-kb A"})
        await client.call_tool("create_knowledge_base", {"name": kb_b, "description": "multi-kb B"})
        await http.post(f"{BASE}/api/kb/{kb_a}/dois", json={"dois": ["10.48550/arxiv.2309.08532"]})
        await http.post(f"{BASE}/api/kb/{kb_b}/dois", json={"dois": ["10.48550/arxiv.2310.11511"]})

    r = await client.call_tool("generate_report", {
        "kb_name": kb_a, "kb_names": [kb_a, kb_b],
        "query": "How can retrieval-augmented generation be improved?",
        "mode": "basic",
    })
    d = _extract(r)
    sources = d.get("sources") or []
    out = {
        "success": d.get("success"),
        "source_count": len(sources),
        "kb_names_in_sources": list({s.get("kb_name") for s in sources if s.get("kb_name")}),
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, sources={out['source_count']}, kbs={out['kb_names_in_sources']}")
    return out


async def case_R7_deep_research(client):
    """deep_research mode (formerly profound) — iteration_count + completion_reason in response."""
    print("\n=== R-7: deep_research mode (F-17 + new metadata) ===")
    kb = "r3-r7-deep"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "deep_research test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={
            "dois": ["10.48550/arxiv.2309.08532", "10.48550/arxiv.2310.11511"]
        })

    t0 = time.monotonic()
    r = await client.call_tool("generate_report", {
        "kb_name": kb,
        "query": "How can retrieval-augmented generation be improved?",
        "mode": "deep_research",
    })
    elapsed = time.monotonic() - t0
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "mode_used": d.get("mode"),
        "iteration_count": d.get("iteration_count"),
        "completion_reason": d.get("completion_reason"),
        "report_chars": len(d.get("report") or ""),
        "elapsed_s": round(elapsed, 1),
        "error": d.get("error"),
        "diagnostic": d.get("diagnostic"),
    }
    print(f"  success={out['success']}, mode={out['mode_used']}, iterations={out['iteration_count']}, reason={out['completion_reason']}")
    print(f"  report chars={out['report_chars']}, elapsed={out['elapsed_s']}s")
    if out.get("diagnostic"):
        print(f"  diagnostic: {out['diagnostic']}")
    return out


async def case_R12_recency(client):
    """Recency weighting on diverse-year corpus."""
    print("\n=== R-12: Recency weighting ===")
    kb = "r3-r12-recency"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "recency test"})
        dois = [
            "10.48550/arxiv.2005.11401",  # RAG 2020
            "10.48550/arxiv.2310.11511",  # Self-RAG 2023
            "10.48550/arxiv.2403.10131",  # C-RAG 2024
            "10.48550/arxiv.1810.04805",  # BERT 2018
        ]
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": dois})

    query = "How can retrieval-augmented language models be improved?"
    out = {"queries": {}}
    for rw in (0.0, 0.9):
        r = await client.call_tool("generate_report", {
            "kb_name": kb, "query": query, "mode": "basic", "recency_weight": rw,
        })
        d = _extract(r)
        years = [s.get("year") for s in (d.get("sources") or [])][:4]
        out["queries"][f"rw_{rw}"] = {"success": d.get("success"), "top_years": years}
        print(f"  rw={rw}: top source years = {years}")
    return out


async def case_R13_backward_cite_graph(client):
    """Backward cite-graph expansion (F-29 fixed: arXiv seeds now return results)."""
    print("\n=== R-13: Cite-graph backward (F-29) ===")
    kb = "r3-r13-backward"
    seed_doi = "10.48550/arxiv.2005.11401"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "backward snowball"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": [seed_doi]})

    t0 = time.monotonic()
    r = await client.call_tool("expand_kb_via_citations", {
        "kb_name": kb, "seed_dois": [seed_doi],
        "direction": "backward", "max_per_seed": 8,
    })
    elapsed = time.monotonic() - t0
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "papers_added": d.get("papers_added"),
        "raw_hits": d.get("raw_hits"),
        "unique_dois": d.get("unique_dois"),
        "elapsed_s": round(elapsed, 1),
        "error": d.get("error"),
    }
    print(f"  papers_added={out['papers_added']}, raw_hits={out['raw_hits']}, unique={out['unique_dois']}, elapsed={out['elapsed_s']}s")
    return out


async def case_R14_conversation_fts(client):
    """Conversation FTS5 search."""
    print("\n=== R-14: Conversation FTS5 search ===")
    kb = "r3-r14-fts"
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "FTS test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": ["10.48550/arxiv.2309.08532"]})
        # Ask a question to create conversation
        chat_payload = {"query": "What is EvoPrompt about?", "kb_name": kb, "mode": "basic"}
        await http.post(f"{BASE}/api/chat", json=chat_payload, timeout=60.0)
        # Search FTS5
        resp = await http.get(f"{BASE}/api/conversations/search?q=EvoPrompt")
        data = resp.json()

    out = {"hits": len(data.get("conversations", data.get("results", []))), "raw_keys": list(data.keys())}
    print(f"  FTS hits: {out['hits']}, response keys: {out['raw_keys']}")
    return out


async def case_R15_kb_export(client):
    """KB export to Obsidian zip."""
    print("\n=== R-15: KB export ===")
    kb = "r3-r15-export"
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "export test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": ["10.48550/arxiv.2309.08532"]})
        resp = await http.get(f"{BASE}/api/kb/{kb}/export?format=obsidian-vault")

    out = {
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "content_length": len(resp.content),
    }
    print(f"  status={out['status_code']}, ct={out['content_type']}, size={out['content_length']} bytes")
    return out


async def case_R16_get_paper_content(client):
    """get_paper_content MCP tool — full_text field present (F-26 regression)."""
    print("\n=== R-16: get_paper_content ===")
    kb = "r3-r16-content"
    doi = "10.48550/arxiv.2309.08532"
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "content test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": [doi]})

    r = await client.call_tool("get_paper_content", {"doi": doi})
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "content_type": d.get("content_type"),
        "sections": d.get("sections"),
        "full_text_length": d.get("full_text_length"),
        "has_full_text": "full_text" in d and bool(d.get("full_text")),
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, ct={out['content_type']}, sections={out['sections']}, full_text_len={out['full_text_length']}, has_full_text={out['has_full_text']}")
    return out


async def case_R17_kb_metadata(client):
    """KB metadata round trip: create → list → get → delete."""
    print("\n=== R-17: KB metadata round trip ===")
    kb = "r3-r17-meta"
    async with httpx.AsyncClient(timeout=60.0) as http:
        await _cleanup_kb(http, kb)
        create_r = await client.call_tool("create_knowledge_base", {"name": kb, "description": "meta test"})
        create_d = _extract(create_r)

        list_r = await client.call_tool("list_knowledge_bases", {})
        list_d = _extract(list_r)
        kbs_in_list = [k for k in (list_d.get("knowledge_bases") or []) if k.get("name") == kb or k == kb]

        detail_resp = await http.get(f"{BASE}/api/kb/{kb}")
        detail = detail_resp.json()

        del_resp = await http.delete(f"{BASE}/api/kb/{kb}")

    out = {
        "create_ok": create_d.get("success"),
        "kb_in_list": len(kbs_in_list) > 0,
        "detail_embedding_model": detail.get("embedding_model"),
        "delete_status": del_resp.status_code,
    }
    print(f"  create={out['create_ok']}, in_list={out['kb_in_list']}, emb={out['detail_embedding_model']}, delete={out['delete_status']}")
    return out


async def case_R18_search_kb_direct(client):
    """search_knowledge_base — chunk metadata fields correct (F-27 regression)."""
    print("\n=== R-18: search_knowledge_base direct ===")
    kb = "r3-r18-search"
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "search test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": ["10.48550/arxiv.2309.08532"]})

    r = await client.call_tool("search_knowledge_base", {"kb_name": kb, "query": "evolutionary algorithm"})
    d = _extract(r)
    chunks = d.get("chunks") or []
    first = chunks[0] if chunks else {}
    out = {
        "success": d.get("success"),
        "chunk_count": len(chunks),
        "first_paper_id": first.get("paper_id"),
        "first_title": (first.get("title") or "")[:60],
        "first_chunk_text_is_dict_repr": (first.get("chunk_text") or "").startswith("{'"),
        "has_relevance_score": "relevance_score" in first,
        "has_kb_name": "kb_name" in first,
    }
    print(f"  chunks={out['chunk_count']}, paper_id={out['first_paper_id']}, title={out['first_title']!r}")
    print(f"  text_is_dict_repr={out['first_chunk_text_is_dict_repr']}, has_score={out['has_relevance_score']}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# NEW FEATURE CASES
# ──────────────────────────────────────────────────────────────────────────────

async def case_N1_claim_graph_build(client):
    """Build claim graph on a 1-paper KB, check status."""
    print("\n=== N-1: Claim graph build + status ===")
    kb = "r3-n1-claimgraph"
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "claim graph test"})
        # Use EvoPrompt — arXiv full text so claims can be extracted
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": ["10.48550/arxiv.2309.08532"]})

    # Build claim graph
    t0 = time.monotonic()
    r_build = await client.call_tool("build_claim_graph", {"kb_name": kb})
    elapsed = time.monotonic() - t0
    build_d = _extract(r_build)

    # Check status
    r_status = await client.call_tool("claim_graph_status", {"kb_name": kb})
    status_d = _extract(r_status)

    out = {
        "build": {
            "success": build_d.get("success"),
            "claims_added": build_d.get("claims_added"),
            "edges_added": build_d.get("edges_added"),
            "pairs_classified": build_d.get("pairs_classified"),
            "papers_processed": build_d.get("papers_processed"),
            "duration_s": build_d.get("duration_seconds"),
            "error": build_d.get("error"),
        },
        "status": {
            "success": status_d.get("success"),
            "paper_count": status_d.get("paper_count"),
            "last_build_iso": status_d.get("last_build_iso"),
            "schema_drift": status_d.get("schema_drift"),
        },
        "wall_elapsed_s": round(elapsed, 1),
    }
    print(f"  build: claims={out['build']['claims_added']}, edges={out['build']['edges_added']}, pairs={out['build']['pairs_classified']}, papers={out['build']['papers_processed']}")
    print(f"  status: paper_count={out['status']['paper_count']}, last_build={out['status']['last_build_iso']}, drift={out['status']['schema_drift']}")
    print(f"  elapsed: {out['wall_elapsed_s']}s")
    return out


async def case_N2_query_claim_graph(client):
    """Query claim graph with papers_with_claim_pattern."""
    print("\n=== N-2: Query claim graph ===")
    kb = "r3-n1-claimgraph"  # Reuse KB from N-1

    # Try papers_with_claim_pattern — subject=None means 'all'
    r = await client.call_tool("query_claim_graph", {
        "kb_name": kb,
        "query_name": "papers_with_claim_pattern",
        "kwargs": {},
    })
    d = _extract(r)
    rows = d.get("rows") or []
    out = {
        "success": d.get("success"),
        "query": d.get("query"),
        "row_count": len(rows),
        "sample_row": rows[0] if rows else None,
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, query={out['query']}, rows={out['row_count']}")
    if out["sample_row"]:
        print(f"  sample: {str(out['sample_row'])[:120]}")

    # Also try claims_supporting with a broad subject
    r2 = await client.call_tool("query_claim_graph", {
        "kb_name": kb,
        "query_name": "claims_supporting",
        "kwargs": {"subject_or_iri": "LLM"},
    })
    d2 = _extract(r2)
    out["claims_supporting_LLM_count"] = len(d2.get("rows") or [])
    print(f"  claims_supporting('LLM'): {out['claims_supporting_LLM_count']} rows")
    return out


async def case_N3_claim_links(client):
    """get_claim_links — retrieve links for a specific claim IRI."""
    print("\n=== N-3: Get claim links ===")
    kb = "r3-n1-claimgraph"

    # First find a claim IRI from the graph
    r = await client.call_tool("query_claim_graph", {
        "kb_name": kb,
        "query_name": "papers_with_claim_pattern",
        "kwargs": {},
    })
    d = _extract(r)
    rows = d.get("rows") or []
    if not rows:
        return {"skipped": True, "reason": "no claims in graph (N-1 may have returned 0 claims)"}

    # Use the first claim IRI from rows — it should have 'claim' key
    first_claim = rows[0]
    claim_iri = first_claim.get("claim") or first_claim.get("claim_iri") or str(first_claim)
    print(f"  Using claim_iri: {str(claim_iri)[:80]}")

    r2 = await client.call_tool("get_claim_links", {"kb_name": kb, "claim_iri": str(claim_iri)})
    d2 = _extract(r2)
    links = d2.get("links") or []
    out = {
        "success": d2.get("success"),
        "claim_iri": str(claim_iri)[:80],
        "link_count": len(links),
        "sample_link": links[0] if links else None,
        "error": d2.get("error"),
    }
    print(f"  success={out['success']}, links={out['link_count']}")
    return out


async def case_N4_claim_graph_export(client):
    """claim_graph_export — check N-Quads format fix (was broken in Turtle/N-Triples)."""
    print("\n=== N-4: Claim graph export ===")
    kb = "r3-n1-claimgraph"

    r = await client.call_tool("claim_graph_export", {"kb_name": kb})
    d = _extract(r)
    export_data = d.get("export") or d.get("data") or d.get("content")
    export_str = str(export_data or "")

    # N-Quads lines look like: <s> <p> <o> <g> .
    # Check the export format is valid N-Quads (lines ending in .)
    lines = [l for l in export_str.splitlines() if l.strip() and not l.strip().startswith("#")]
    valid_nquads_lines = sum(1 for l in lines if l.rstrip().endswith("."))
    out = {
        "success": d.get("success"),
        "export_chars": len(export_str),
        "total_lines": len(lines),
        "valid_nquads_lines": valid_nquads_lines,
        "format": d.get("format"),
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, chars={out['export_chars']}, lines={out['total_lines']}, valid_nquads={out['valid_nquads_lines']}")
    return out


async def case_N5_generate_report_metadata(client):
    """generate_report returns iteration_count + completion_reason (Issue 6)."""
    print("\n=== N-5: generate_report iteration_count + completion_reason ===")
    kb = "r3-r7-deep"  # Reuse deep_research KB

    r = await client.call_tool("generate_report", {
        "kb_name": kb,
        "query": "What is the key contribution of EvoPrompt?",
        "mode": "basic",
    })
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "has_iteration_count": "iteration_count" in d,
        "iteration_count": d.get("iteration_count"),
        "has_completion_reason": "completion_reason" in d,
        "completion_reason": d.get("completion_reason"),
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, iteration_count={out['iteration_count']}, completion_reason={out['completion_reason']}")
    return out


async def case_N6_early_return_diagnostic(client):
    """Early-return diagnostic dict in MCP tool responses (Issue 5)."""
    print("\n=== N-6: Early-return diagnostic dict ===")
    # Use an empty KB — generate_report should early-return with diagnostic
    kb = "r3-n6-empty"
    async with httpx.AsyncClient(timeout=30.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "empty for diagnostic test"})

    r = await client.call_tool("generate_report", {
        "kb_name": kb,
        "query": "What is the key contribution?",
        "mode": "basic",
    })
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "has_diagnostic": "diagnostic" in d,
        "diagnostic": d.get("diagnostic"),
        "error": d.get("error"),
    }
    print(f"  success={out['success']}, has_diagnostic={out['has_diagnostic']}")
    if out.get("diagnostic"):
        print(f"  diagnostic: {out['diagnostic']}")
    return out


async def case_N7_literature_survey_seed_filter(client):
    """Literature survey seed filter: seeds kept even if already in KB (Issue 3)."""
    print("\n=== N-7: Literature survey seed filter ===")
    kb = "r3-n7-survey"
    seed_doi = "10.48550/arxiv.2309.08532"  # EvoPrompt — will be seeded

    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "survey seed test"})
        # Add the seed paper to the KB first
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": [seed_doi]})

    # Now run literature survey — note: seed_dois filter not yet implemented in
    # search_literature, so we run without it and check whatever results are returned.
    r = await client.call_tool("search_literature", {
        "query": "LLM prompt optimization with evolutionary algorithms",
        "max_results": 10,
    })
    search_d = _extract(r)
    papers = search_d.get("papers") or []
    seed_in_results = any(
        (p.get("doi") or "").endswith("2309.08532") for p in papers
    )

    # Also run generate_report in literature_survey mode
    r2 = await client.call_tool("generate_report", {
        "kb_name": kb,
        "query": "LLM prompt optimization with evolutionary algorithms",
        "mode": "literature_survey",
    })
    d2 = _extract(r2)

    out = {
        "search_papers": len(papers),
        "seed_in_search_results": seed_in_results,
        "seed_dois_filter_note": "seed_dois filter not yet implemented in search_literature",
        "survey_success": d2.get("success"),
        "survey_error": d2.get("error"),
        "survey_diagnostic": d2.get("diagnostic"),
    }
    print(f"  search papers={out['search_papers']}, seed_in_results={out['seed_in_search_results']}")
    print(f"  survey success={out['survey_success']}, diagnostic={out['survey_diagnostic']}")
    return out


async def case_N8_f30_attempts_abstract_only(client):
    """F-30: attempts trail on abstract-only successes."""
    print("\n=== N-8: F-30 — attempts on abstract-only ingest ===")
    kb = "r3-n8-abstract"
    # IEEE TKDE — typically abstract-only (no OA PDF)
    doi = "10.1109/TKDE.2023.3271425"
    async with httpx.AsyncClient(timeout=200.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "abstract-only test"})
        resp = await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": [doi]})
        data = resp.json()

    # Look for metadata_only[] with attempts
    meta_only = data.get("metadata_only") or []
    has_attempts = any("attempts" in m for m in meta_only)
    out = {
        "added_papers": data.get("added_papers"),
        "added_with_full_text": data.get("added_with_full_text"),
        "added_metadata_only": data.get("added_metadata_only"),
        "metadata_only_entries": len(meta_only),
        "has_attempts_in_metadata_only": has_attempts,
        "sample_attempts": meta_only[0].get("attempts") if meta_only else None,
    }
    print(f"  added={out['added_papers']}, full={out['added_with_full_text']}, meta={out['added_metadata_only']}")
    print(f"  metadata_only entries={out['metadata_only_entries']}, has_attempts={out['has_attempts_in_metadata_only']}")
    if out["sample_attempts"]:
        print(f"  attempts: {out['sample_attempts'][:3]}")
    return out


async def case_N9_f28_outcome_split(client):
    """F-28: DOI ingest outcome split (added_with_full_text / added_metadata_only / failed)."""
    print("\n=== N-9: F-28 — DOI ingest outcome split ===")
    kb = "r3-n9-split"
    dois = [
        "10.48550/arxiv.2309.08532",         # full text expected (arXiv)
        "10.1109/TKDE.2023.3271425",          # abstract-only (IEEE paywall)
        "10.99999/totally.fake.xyz.999",      # should fail
    ]
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "outcome split test"})
        resp = await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": dois})
        data = resp.json()

    out = {
        "added_papers": data.get("added_papers"),
        "added_with_full_text": data.get("added_with_full_text"),
        "added_metadata_only": data.get("added_metadata_only"),
        "failed_count": len(data.get("failed") or []),
        "failed_dois": [f.get("doi") or f.get("key") for f in (data.get("failed") or [])],
        "has_outcome_split": "added_with_full_text" in data,
    }
    print(f"  added={out['added_papers']}: full={out['added_with_full_text']}, meta={out['added_metadata_only']}, failed={out['failed_count']}")
    print(f"  failed_dois={out['failed_dois']}")
    print(f"  has_outcome_split field: {out['has_outcome_split']}")
    return out


async def case_N10_f29_backward_arxiv(client):
    """F-29: Backward cite-graph for arXiv seeds now returns non-empty results."""
    print("\n=== N-10: F-29 — backward cite-graph arXiv seed ===")
    kb = "r3-n10-backward"
    seed = "10.48550/arxiv.2005.11401"  # RAG paper
    async with httpx.AsyncClient(timeout=300.0) as http:
        await _cleanup_kb(http, kb)
        await client.call_tool("create_knowledge_base", {"name": kb, "description": "backward test"})
        await http.post(f"{BASE}/api/kb/{kb}/dois", json={"dois": [seed]})

    t0 = time.monotonic()
    r = await client.call_tool("expand_kb_via_citations", {
        "kb_name": kb, "seed_dois": [seed],
        "direction": "backward", "max_per_seed": 10,
    })
    elapsed = time.monotonic() - t0
    d = _extract(r)
    out = {
        "success": d.get("success"),
        "papers_added": d.get("papers_added"),
        "raw_hits": d.get("raw_hits"),
        "unique_dois": d.get("unique_dois"),
        "elapsed_s": round(elapsed, 1),
        "error": d.get("error"),
    }
    print(f"  papers_added={out['papers_added']}, raw_hits={out['raw_hits']}, unique={out['unique_dois']}, elapsed={out['elapsed_s']}s")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    results = {}
    async with Client(MCP_URL) as client:
        cases = [
            ("smoke",       case_smoke),
            ("R1",          case_R1_search_screening),
            ("R2",          case_R2_doi_ingest),
            ("R4",          case_R4_bibtex_mixed),
            ("R5",          case_R5_url_batch),
            ("R6",          case_R6_multi_kb),
            ("R7",          case_R7_deep_research),
            ("R12",         case_R12_recency),
            ("R13",         case_R13_backward_cite_graph),
            ("R14",         case_R14_conversation_fts),
            ("R15",         case_R15_kb_export),
            ("R16",         case_R16_get_paper_content),
            ("R17",         case_R17_kb_metadata),
            ("R18",         case_R18_search_kb_direct),
            ("N1",          case_N1_claim_graph_build),
            ("N2",          case_N2_query_claim_graph),
            ("N3",          case_N3_claim_links),
            ("N4",          case_N4_claim_graph_export),
            ("N5",          case_N5_generate_report_metadata),
            ("N6",          case_N6_early_return_diagnostic),
            ("N7",          case_N7_literature_survey_seed_filter),
            ("N8",          case_N8_f30_attempts_abstract_only),
            ("N9",          case_N9_f28_outcome_split),
            ("N10",         case_N10_f29_backward_arxiv),
        ]
        for name, fn in cases:
            try:
                results[name] = await fn(client)
            except Exception as exc:
                print(f"\n  ERROR in {name}: {exc}")
                results[name] = {"error": str(exc)}

    out_path = OUT / "audit_round3.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n\n✅ Audit complete. Results saved to {out_path}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
