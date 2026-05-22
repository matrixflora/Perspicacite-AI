"""PMC efetch JATS fallback for PMCIDs outside the OA bulk subset.

Author manuscripts and other "restricted-by pmc" articles are in PMC but not
in the OA Open Data S3 bucket, so the S3 XML/text URLs 404. NCBI efetch still
serves the full JATS for individual retrieval; the pipeline must fall back to
it before giving up.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from perspicacite.pipeline.download import pmc

# Minimal JATS that the existing extractors can parse: an abstract, one body
# section with >200 chars of text, and one reference.
_EFETCH_JATS = (
    b'<?xml version="1.0"?><pmc-articleset><article>'
    b"<front><article-meta><abstract><p>Short abstract.</p></abstract>"
    b"</article-meta></front>"
    b"<body><sec><title>Introduction</title><p>" + (b"content " * 60) + b"</p></sec></body>"
    b"<back><ref-list><ref id=\"R1\"><mixed-citation>"
    b"Doe J. A cited work. Journal 2020.</mixed-citation></ref></ref-list></back>"
    b"</article></pmc-articleset>"
)


@pytest.mark.asyncio
async def test_efetch_fallback_used_when_s3_oa_misses(tmp_path, monkeypatch):
    monkeypatch.setattr(pmc, "_CACHE_DIR", tmp_path)
    s3_xml_miss = Mock(status_code=404, content=b"", text="")
    s3_txt_miss = Mock(status_code=404, content=b"", text="")
    efetch_hit = Mock(status_code=200, content=_EFETCH_JATS, text=_EFETCH_JATS.decode())

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[s3_xml_miss, s3_txt_miss, efetch_hit])

    with patch.object(pmc, "_resolve_pmcid", AsyncMock(return_value="PMC11042918")):
        text, _sections = await pmc.get_fulltext_from_pmc("10.1038/x", http_client=client)

    assert text is not None and len(text) > 200
    assert client.get.call_count == 3
    efetch_url = client.get.call_args_list[2].args[0]
    assert "efetch.fcgi" in efetch_url
    assert "id=11042918" in efetch_url


@pytest.mark.asyncio
async def test_efetch_not_called_when_s3_xml_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(pmc, "_CACHE_DIR", tmp_path)
    s3_xml_hit = Mock(status_code=200, content=_EFETCH_JATS, text=_EFETCH_JATS.decode())

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[s3_xml_hit])

    with patch.object(pmc, "_resolve_pmcid", AsyncMock(return_value="PMC11042918")):
        text, _ = await pmc.get_fulltext_from_pmc("10.1038/x", http_client=client)

    assert text is not None and len(text) > 200
    # S3 XML hit on first call: no S3-txt, no efetch.
    assert client.get.call_count == 1


@pytest.mark.asyncio
async def test_pmc_route_runs_when_discovery_finds_no_pmcid(tmp_path):
    """Regression: the structured PMC route must run even when discovery
    (OpenAlex/Unpaywall) returns pmcid=None. get_fulltext_from_pmc resolves
    its own PMCID via Europe PMC, so gating on discovery's PMCID silently
    skipped papers that are in PMC but unindexed by OpenAlex (e.g. the CRISPRi
    paper 10.1038/s41587-022-01531-8 → PMC11042918).
    """
    from unittest.mock import patch

    from perspicacite.pipeline.download import unified
    from perspicacite.pipeline.download.base import PaperDiscovery

    disc = PaperDiscovery(doi="10.1038/x", title="T", pmcid=None, is_oa=True)
    pmc_text = "Full text resolved via PMC self-resolution. " * 20

    with (
        patch(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            new_callable=AsyncMock,
            return_value=disc,
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
            new_callable=AsyncMock,
            return_value=(pmc_text, {"Introduction": "intro"}),
        ) as mock_pmc,
        patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
    ):
        result = await unified.retrieve_paper_content("10.1038/x", http_client=AsyncMock())

    mock_pmc.assert_awaited_once()
    assert result.success is True
    assert result.content_source == "pmc"
