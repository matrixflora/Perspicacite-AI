"""ASB-aligned accession mining — vendored verbatim."""

from __future__ import annotations

import pytest


def test_mine_known_kinds():
    from perspicacite.pipeline.external.accessions import mine_accessions
    txt = (
        "We deposited reads at PRIDE (PXD012345) and intermediate spectra "
        "at MassIVE MSV000089123. The transcriptomics is at GEO GSE123456 "
        "and BioProject PRJNA987654 with run SRR1234567."
    )
    out = mine_accessions(txt)
    kinds = {r["kind"] for r in out}
    assert {"pride", "massive", "geo_series", "bioproject", "sra_run"} <= kinds


def test_dedup_and_order():
    from perspicacite.pipeline.external.accessions import mine_accessions
    txt = "PXD012345 mentioned twice: PXD012345; also MTBLS123."
    out = mine_accessions(txt)
    assert sum(1 for r in out if r["accession"] == "PXD012345") == 1
    kinds_in_order = [r["kind"] for r in out]
    assert kinds_in_order.index("pride") < kinds_in_order.index("metabolights")


def test_empty_and_no_match():
    from perspicacite.pipeline.external.accessions import mine_accessions
    assert mine_accessions("") == []
    assert mine_accessions("no accessions here, just prose.") == []


def test_record_shape():
    from perspicacite.pipeline.external.accessions import mine_accessions
    out = mine_accessions("see PXD012345 in the SI")
    assert len(out) == 1
    r = out[0]
    assert set(r.keys()) == {"kind", "accession", "url", "evidence_span"}
    assert r["url"].endswith("/projects/PXD012345")
    assert "PXD012345" in r["evidence_span"]
