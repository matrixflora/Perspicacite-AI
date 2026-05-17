"""Round-3 live audit — verify F-28, F-29, F-30 follow-up fixes.

  R-28: BibTeX endpoint reports added_with_full_text / added_metadata_only,
        with bad-DOI entries surfaced in failed[] + metadata_only[]
  R-29: Cite-graph backward direction returns hits for an arXiv DOI seed
        (via SS fallback; survives bad SS API key by retrying unauth)
  R-30: Successful abstract-only ingests surface the attempts trail in
        the metadata_only[] section of the response
"""
import asyncio
import json
import time
from pathlib import Path

import httpx
from fastmcp import Client

BASE = "http://localhost:8000"
MCP_URL = f"{BASE}/mcp"
OUT = Path(__file__).parent
RESULTS: dict = {}


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
            return {"raw_text": payload[:1500]}
    return payload or {}


async def case_R28_bibtex_split(client):
    """BibTeX with mixed entries: one valid DOI, one bad DOI, one metadata-only."""
    print("\n=== R-28: BibTeX outcome split (full_text / metadata_only / failed) ===")
    kb = "asb-r28-bibtex"
    async with httpx.AsyncClient(timeout=120.0) as http:
        await http.delete(f"{BASE}/api/kb/{kb}")
        await http.post(f"{BASE}/api/kb", json={"name": kb, "description": "R-28 mixed BibTeX"})

        bibtex = r"""
@article{good_doi,
  title={Attention Is All You Need},
  author={Vaswani, A. and Shazeer, N.},
  year={2017},
  doi={10.48550/arxiv.1706.03762}
}
@article{bad_doi,
  title={A fake paper with a garbage DOI},
  author={Nobody, A.},
  year={2020},
  doi={10.99999/this.is.not.a.real.doi.xyz123}
}
@article{no_doi_metadata,
  title={Some paper with no DOI but a useful title},
  author={Smith, J.},
  year={2019}
}
"""
        t0 = time.time()
        r = await http.post(
            f"{BASE}/api/kb/{kb}/bibtex",
            json={"bibtex": bibtex},
            timeout=180.0,
        )
        elapsed = time.time() - t0
        payload = r.json()

    # Expectations:
    # - added_papers >= 1
    # - added_with_full_text + added_metadata_only == added_papers
    # - failed contains the bad DOI
    # - metadata_only entries carry an attempts list (even if empty for the no-doi entry)
    summary = {
        "elapsed_s": round(elapsed, 2),
        "total_entries": payload.get("total_entries"),
        "added_papers": payload.get("added_papers"),
        "added_with_full_text": payload.get("added_with_full_text"),
        "added_metadata_only": payload.get("added_metadata_only"),
        "skipped_duplicates": payload.get("skipped_duplicates"),
        "failed_count": len(payload.get("failed") or []),
        "metadata_only_count": len(payload.get("metadata_only") or []),
        "failed": payload.get("failed"),
        "metadata_only": payload.get("metadata_only"),
        "pdf_download": payload.get("pdf_download"),
    }
    has_split = (
        payload.get("added_with_full_text") is not None
        and payload.get("added_metadata_only") is not None
    )
    counts_match = False
    if has_split:
        counts_match = (
            (payload.get("added_with_full_text") or 0)
            + (payload.get("added_metadata_only") or 0)
            == payload.get("added_papers")
        )
    bad_doi_in_failed = any(
        "this.is.not.a.real.doi.xyz123" in (e.get("key") or "")
        for e in (payload.get("failed") or [])
    )
    summary["asserts"] = {
        "has_added_split_fields": has_split,
        "split_counts_sum_correct": counts_match,
        "bad_doi_in_failed": bad_doi_in_failed,
        "metadata_only_present": len(payload.get("metadata_only") or []) > 0,
    }
    print(json.dumps(summary["asserts"], indent=2))
    RESULTS["R28_bibtex_split"] = summary


async def case_R29_arxiv_backward(client):
    """Cite-graph BACKWARD with an arXiv DOI seed must produce hits via SS fallback."""
    print("\n=== R-29: Cite-graph backward direction for arXiv seed ===")
    kb = "asb-r29-backward"
    seed_doi = "10.48550/arxiv.2005.11401"

    async with httpx.AsyncClient(timeout=180.0) as http:
        await http.delete(f"{BASE}/api/kb/{kb}")
    await client.call_tool("create_knowledge_base", {
        "name": kb, "description": "R-29 backward cite-graph test",
    })
    seed_resp = await client.call_tool("add_dois_to_kb", {
        "kb_name": kb, "dois": [seed_doi],
    })
    seed_payload = _extract(seed_resp)

    t0 = time.time()
    # Backward direction only, smallish cap to keep this snappy
    resp = await client.call_tool("expand_kb_via_citations", {
        "kb_name": kb,
        "direction": "backward",
        "max_per_seed": 8,
        "dry_run": True,  # we just want to see raw_hits, not chunk-ingest 8 papers
    })
    elapsed = time.time() - t0
    payload = _extract(resp)

    summary = {
        "elapsed_s": round(elapsed, 2),
        "seed_doi": seed_doi,
        "seed_added": seed_payload.get("added_papers"),
        "raw_hits": payload.get("raw_hits"),
        "unique_dois": payload.get("unique_dois"),
        "ingested_dois_count": len(payload.get("ingested_dois") or []),
        "success": payload.get("success"),
        "asserts": {
            "backward_returns_nonzero_hits": (payload.get("raw_hits") or 0) > 0,
        },
    }
    print(json.dumps(summary["asserts"], indent=2))
    print(f"raw_hits={payload.get('raw_hits')}, unique_dois={payload.get('unique_dois')}")
    RESULTS["R29_arxiv_backward"] = summary


async def case_R30_abstract_only_attempts(client):
    """Wiley / Elsevier DOI that degrades to abstract-only: attempts trail surfaced."""
    print("\n=== R-30: PaperContent.attempts surfaced on abstract-only ingest ===")
    kb = "asb-r30-abstract"
    # Try a few candidate DOIs that typically degrade to abstract-only when
    # the publisher API tokens aren't configured. We pick the first one that
    # the pipeline actually treats as metadata-only.
    candidates = [
        "10.1021/jacs.0c10116",              # JACS (ACS) — not on PMC
        "10.1287/mnsc.2022.4595",            # Management Science (INFORMS)
        "10.1109/TKDE.2023.3271425",         # IEEE TKDE
        "10.1002/jcc.27079",                 # J. Comp. Chem. (Wiley)
        "10.1093/mnras/stad1838",            # MNRAS (OUP, astro)
        "10.1080/00018732.2020.1854537",     # Advances in Physics (Taylor & Francis)
    ]

    chosen_doi = None
    payload = None
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=180.0) as http:
        await http.delete(f"{BASE}/api/kb/{kb}")
        await http.post(f"{BASE}/api/kb", json={"name": kb, "description": "R-30 attempts trail"})

        for doi in candidates:
            t0 = time.time()
            r = await http.post(
                f"{BASE}/api/kb/{kb}/dois",
                json={"dois": [doi]},
                timeout=180.0,
            )
            elapsed = time.time() - t0
            payload = r.json()
            if (payload.get("added_metadata_only") or 0) >= 1:
                chosen_doi = doi
                break
            # cleanup + retry with next candidate
            print(f"  - {doi} → added_full={payload.get('added_with_full_text')}, "
                  f"meta={payload.get('added_metadata_only')} (skipping)")
            await http.delete(f"{BASE}/api/kb/{kb}")
            await http.post(f"{BASE}/api/kb", json={"name": kb, "description": "R-30 attempts trail"})

    doi = chosen_doi or candidates[0]

    metadata_only = payload.get("metadata_only") or []
    target = next(
        (m for m in metadata_only if (m.get("doi") or "").lower() == doi.lower()),
        None,
    )

    summary = {
        "elapsed_s": round(elapsed, 2),
        "added_papers": payload.get("added_papers"),
        "added_with_full_text": payload.get("added_with_full_text"),
        "added_metadata_only": payload.get("added_metadata_only"),
        "metadata_only_entries": metadata_only,
        "asserts": {
            "added_as_metadata_only": (payload.get("added_metadata_only") or 0) >= 1,
            "target_in_metadata_only": target is not None,
            "attempts_list_present": target is not None and isinstance(target.get("attempts"), list),
            "attempts_nonempty": target is not None and len(target.get("attempts") or []) > 0,
            "content_type_recorded": target is not None and bool(target.get("content_type")),
        },
    }
    print(json.dumps(summary["asserts"], indent=2))
    RESULTS["R30_abstract_attempts"] = summary


async def main():
    async with Client(MCP_URL) as client:
        await case_R28_bibtex_split(client)
        await case_R29_arxiv_backward(client)
        await case_R30_abstract_only_attempts(client)

    out = OUT / "round3_followups.json"
    out.write_text(json.dumps(RESULTS, indent=2, default=str))
    print(f"\nWrote {out}")

    print("\n=== Summary ===")
    for case, data in RESULTS.items():
        ok = all(data.get("asserts", {}).values())
        print(f"  {case}: {'PASS' if ok else 'FAIL'} — {data.get('asserts')}")


if __name__ == "__main__":
    asyncio.run(main())
