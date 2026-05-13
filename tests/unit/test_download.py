"""Tests for PDF download utilities."""

import pytest
import httpx
from unittest.mock import Mock, AsyncMock, patch

from perspicacite.pipeline.download import (
    PDFDownloader,
    get_open_access_url,
    get_pdf_from_alternative_endpoint,
    retrieve_paper_content,
)
from perspicacite.pipeline.download.base import PaperContent, PaperDiscovery
from perspicacite.pipeline.download.discovery import discover_paper_sources


class TestPDFDownloader:
    """Tests for PDFDownloader."""

    @pytest.fixture
    def downloader(self):
        return PDFDownloader()

    @pytest.mark.asyncio
    async def test_download_success(self, downloader):
        """Test successful PDF download."""
        mock_response = Mock()
        mock_response.content = b"PDF content here"
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await downloader.download(
            "https://example.com/paper.pdf",
            http_client=mock_client,
        )

        assert result == b"PDF content here"
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_http_error(self, downloader):
        """Test download with HTTP error."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "404",
                request=Mock(),
                response=Mock(status_code=404),
            )
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await downloader.download(
            "https://example.com/notfound.pdf",
            http_client=mock_client,
        )

        assert result is None


class TestAlternativeEndpoint:
    """Tests for alternative endpoint PDF download."""

    @pytest.mark.asyncio
    async def test_get_pdf_from_alternative_endpoint_embed(self):
        """Test finding PDF in embed tag."""
        html_content = """
        <html>
        <body>
            <embed type="application/pdf" src="/download/paper.pdf">
        </body>
        </html>
        """

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()

        mock_pdf_response = Mock()
        mock_pdf_response.content = b"PDF content from embed"
        mock_pdf_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                mock_response,
                mock_pdf_response,
            ]
        )

        result = await get_pdf_from_alternative_endpoint(
            "10.1234/test",
            "https://example.com/",
            http_client=mock_client,
        )

        assert result == b"PDF content from embed"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_get_pdf_from_alternative_endpoint_iframe(self):
        """Test finding PDF in iframe tag."""
        html_content = """
        <html>
        <body>
            <iframe src="https://example.com/file.pdf"></iframe>
        </body>
        </html>
        """

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()

        mock_pdf_response = Mock()
        mock_pdf_response.content = b"PDF content from iframe"
        mock_pdf_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                mock_response,
                mock_pdf_response,
            ]
        )

        result = await get_pdf_from_alternative_endpoint(
            "10.1234/test",
            "https://example.com/",
            http_client=mock_client,
        )

        assert result == b"PDF content from iframe"

    @pytest.mark.asyncio
    async def test_get_pdf_from_alternative_endpoint_link(self):
        """Test finding PDF in a link tag."""
        html_content = """
        <html>
        <body>
            <a href="/files/paper.pdf">Download PDF</a>
        </body>
        </html>
        """

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()

        mock_pdf_response = Mock()
        mock_pdf_response.content = b"PDF content from link"
        mock_pdf_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                mock_response,
                mock_pdf_response,
            ]
        )

        result = await get_pdf_from_alternative_endpoint(
            "10.1234/test",
            "https://example.com/",
            http_client=mock_client,
        )

        assert result == b"PDF content from link"

    @pytest.mark.asyncio
    async def test_get_pdf_from_alternative_endpoint_not_found(self):
        """Test when no PDF is found in the page."""
        html_content = "<html><body>No PDF here</body></html>"

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await get_pdf_from_alternative_endpoint(
            "10.1234/test",
            "https://example.com/",
            http_client=mock_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_pdf_from_alternative_endpoint_http_error(self):
        """Test handling HTTP error."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "404",
                request=Mock(),
                response=Mock(status_code=404),
            )
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await get_pdf_from_alternative_endpoint(
            "10.1234/test",
            "https://example.com/",
            http_client=mock_client,
        )

        assert result is None


class TestRetrievePaperContent:
    """Tests for the unified retrieve_paper_content pipeline."""

    @pytest.mark.asyncio
    async def test_returns_abstract_when_no_full_text(self, monkeypatch, tmp_path):
        """When no full text sources work but abstract is available, return abstract."""
        monkeypatch.setenv("UNPAYWALL_EMAIL", "test@example.com")
        # Use unique DOI to avoid cache collisions
        doi = "10.1234/test-abstract-only-unique-001"

        # Mock OpenAlex returning an abstract
        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "Test Paper",
                "type": "article",
                "open_access": {"is_oa": False, "oa_url": None},
                "ids": {},
                "abstract_inverted_index": {
                    "Test": [0],
                    "abstract": [1],
                    "text": [2],
                    "for": [3],
                    "testing": [4],
                    "purposes": [5],
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_oa_response)

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            result = await retrieve_paper_content(
                doi,
                http_client=mock_client,
            )

        assert result.success is True
        assert result.content_type == "abstract"
        assert result.abstract == "Test abstract text for testing purposes"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_content(self, monkeypatch, tmp_path):
        """When no content at all is available, return none type."""
        monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
        monkeypatch.delenv("OPENALEX_MAILTO", raising=False)
        doi = "10.1234/nonexistent-unique-002"

        # OpenAlex fails
        mock_oa_response = Mock()
        mock_oa_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_oa_response)

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            result = await retrieve_paper_content(
                doi,
                http_client=mock_client,
            )

        assert result.success is False
        assert result.content_type == "none"

    @pytest.mark.asyncio
    async def test_structured_from_pmc(self, monkeypatch, tmp_path):
        """When PMCID is found and PMC has content, return structured."""
        monkeypatch.setenv("UNPAYWALL_EMAIL", "test@example.com")
        doi = "10.1234/pmc-paper-unique-003"

        # Mock OpenAlex returning PMCID
        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "PMC Paper",
                "type": "article",
                "open_access": {"is_oa": True, "oa_url": None},
                "ids": {"pmcid": "PMC12345"},
                "abstract_inverted_index": None,
            }
        )

        # Mock Unpaywall
        mock_up_response = Mock()
        mock_up_response.status_code = 200
        mock_up_response.json = Mock(
            return_value={
                "is_oa": True,
                "best_oa_location": None,
                "oa_locations": [],
            }
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_oa_response, mock_up_response])

        # Mock PMC to return structured text
        with (
            patch(
                "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
                new_callable=AsyncMock,
                return_value=(
                    "This is a long full text from PMC " * 20,
                    {"Introduction": "Some intro text"},
                ),
            ),
            patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
        ):
            result = await retrieve_paper_content(
                doi,
                http_client=mock_client,
            )

        assert result.success is True
        assert result.content_type == "structured"
        assert result.content_source == "pmc"
        assert result.sections == {"Introduction": "Some intro text"}

    @pytest.mark.asyncio
    async def test_arxiv_atom_fallback_fills_missing_authors_year(self, monkeypatch, tmp_path):
        """Regression: when OpenAlex returns title-only for a fresh arXiv paper,
        ``discover_paper_sources`` falls back to the arXiv Atom API and fills
        authors/year so references render correctly.
        """
        monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
        doi = "10.48550/arXiv.2604.27351"

        # OpenAlex returns title only (no authors, no year) — simulates a
        # fresh arXiv preprint that OpenAlex has only partially indexed.
        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "Heterogeneous Scientific Foundation Model Collaboration",
                "type": "preprint",
                "open_access": {"is_oa": False, "oa_url": None},
                "ids": {},
                "authorships": [],
                "publication_year": None,
                "abstract_inverted_index": None,
            }
        )

        # arXiv Atom API returns full metadata for the same paper.
        mock_atom_response = Mock()
        mock_atom_response.status_code = 200
        mock_atom_response.text = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Heterogeneous Scientific Foundation Model Collaboration</title>
    <author><name>First Author</name></author>
    <author><name>Second Author</name></author>
    <author><name>Third Author</name></author>
    <published>2026-04-15T12:00:00Z</published>
  </entry>
</feed>"""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_oa_response, mock_atom_response])

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            disc = await discover_paper_sources(doi, mock_client)

        assert disc.title == "Heterogeneous Scientific Foundation Model Collaboration"
        assert disc.authors == ["First Author", "Second Author", "Third Author"]
        assert disc.year == 2026
        assert disc.arxiv_id == "2604.27351"

    @pytest.mark.asyncio
    async def test_arxiv_atom_skipped_when_openalex_is_complete(self, monkeypatch, tmp_path):
        """The arXiv Atom fallback should NOT fire when OpenAlex already
        returned authors + year, to avoid an unnecessary network call.
        """
        monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
        doi = "10.48550/arXiv.2604.99999"

        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "Already Complete",
                "type": "preprint",
                "open_access": {"is_oa": False, "oa_url": None},
                "ids": {},
                "authorships": [{"author": {"display_name": "Solo Author"}}],
                "publication_year": 2025,
                "abstract_inverted_index": None,
            }
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_oa_response])

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            disc = await discover_paper_sources(doi, mock_client)

        assert disc.authors == ["Solo Author"]
        assert disc.year == 2025
        # OpenAlex was the only call — Atom API was not invoked.
        assert mock_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_arxiv_atom_enriches_stale_cache(self, monkeypatch, tmp_path):
        """Regression: a cached PaperDiscovery written before the Atom fallback
        existed (title only, no authors/year) is enriched on cache hit.
        """
        from perspicacite.pipeline.download.discovery import _write_discovery_cache
        from perspicacite.pipeline.download.base import PaperDiscovery

        monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
        doi = "10.48550/arXiv.2604.88888"

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            # Seed the cache with a stale, partial discovery.
            stale = PaperDiscovery(doi=doi, title="Stale Cached Title", arxiv_id="2604.88888")
            _write_discovery_cache(stale)

            mock_atom_response = Mock()
            mock_atom_response.status_code = 200
            mock_atom_response.text = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Stale Cached Title</title>
    <author><name>New Author</name></author>
    <published>2026-03-01T00:00:00Z</published>
  </entry>
</feed>"""
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_atom_response)

            disc = await discover_paper_sources(doi, mock_client)

        assert disc.authors == ["New Author"]
        assert disc.year == 2026

    @pytest.mark.asyncio
    async def test_pmc_metadata_includes_authors_year(self, monkeypatch, tmp_path):
        """Regression: every PaperContent return site must populate authors+year+title from discovery.

        Before this fix, only the alternative-endpoint path put authors/year into metadata.
        The arxiv_html, pmc, pdf, elsevier, and abstract paths only put `title`, which led
        to "Unknown, None" in the references section.
        """
        monkeypatch.setenv("UNPAYWALL_EMAIL", "test@example.com")
        doi = "10.1234/pmc-meta-prop-005"

        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "Example PMC Paper",
                "type": "article",
                "publication_year": 2024,
                "authorships": [
                    {"author": {"display_name": "Alice Author"}},
                    {"author": {"display_name": "Bob Co-Author"}},
                ],
                "open_access": {"is_oa": True, "oa_url": None},
                "ids": {"pmcid": "PMC9999"},
                "abstract_inverted_index": None,
            }
        )
        mock_up_response = Mock()
        mock_up_response.status_code = 200
        mock_up_response.json = Mock(return_value={"is_oa": True, "oa_locations": []})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_oa_response, mock_up_response])

        with (
            patch(
                "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
                new_callable=AsyncMock,
                return_value=("Full text body. " * 20, {"Introduction": "intro"}),
            ),
            patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
        ):
            result = await retrieve_paper_content(doi, http_client=mock_client)

        assert result.success is True
        assert result.metadata is not None
        assert result.metadata["title"] == "Example PMC Paper"
        assert result.metadata["authors"] == ["Alice Author", "Bob Co-Author"]
        assert result.metadata["year"] == 2024
        assert result.metadata["doi"] == doi

    @pytest.mark.asyncio
    async def test_skips_pdf_when_no_parser(self, monkeypatch, tmp_path):
        """When pdf_parser is None, PDF sources are skipped entirely."""
        monkeypatch.setenv("UNPAYWALL_EMAIL", "test@example.com")
        doi = "10.1234/oa-paper-unique-004"

        # OpenAlex returns OA URL but no parser
        mock_oa_response = Mock()
        mock_oa_response.status_code = 200
        mock_oa_response.json = Mock(
            return_value={
                "title": "OA Paper",
                "type": "article",
                "open_access": {"is_oa": True, "oa_url": "https://publisher.com/paper.pdf"},
                "ids": {},
                "abstract_inverted_index": {
                    "This": [0],
                    "is": [1],
                    "a": [2],
                    "longer": [3],
                    "abstract": [4],
                    "for": [5],
                    "testing": [6],
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_oa_response)

        with patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path):
            result = await retrieve_paper_content(
                doi,
                http_client=mock_client,
                pdf_parser=None,  # No parser
            )

        # Should fall through to abstract (no PDF parsing possible)
        assert result.success is True
        assert result.content_type == "abstract"


class TestUnpaywall:
    """Tests for Unpaywall integration."""

    @pytest.fixture
    def test_email(self):
        """Test email for Unpaywall."""
        return "test@example.com"

    @pytest.mark.asyncio
    async def test_get_open_access_url_found(self, test_email):
        """Test finding OA URL via Unpaywall."""
        mock_response = Mock()
        mock_response.json = Mock(
            return_value={
                "is_oa": True,
                "best_oa_location": {"pdf_url": "https://oa.example.com/paper.pdf"},
            }
        )
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await get_open_access_url(
            "10.1234/test",
            http_client=mock_client,
            email=test_email,
        )

        assert result == "https://oa.example.com/paper.pdf"

    @pytest.mark.asyncio
    async def test_get_open_access_url_not_found(self, test_email):
        """Test when no OA version available."""
        mock_response = Mock()
        mock_response.json = Mock(return_value={"is_oa": False, "best_oa_location": None})
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await get_open_access_url(
            "10.1234/paywalled",
            http_client=mock_client,
            email=test_email,
        )

        assert result is None


class TestCrossrefEnrichmentInUnifiedPipeline:
    """Tests for Crossref gap-fill wiring in the unified retrieval pipeline."""

    @pytest.mark.asyncio
    async def test_discovery_enriched_by_crossref(self, respx_mock, monkeypatch):
        from perspicacite.pipeline.download import retrieve_paper_content
        from perspicacite.pipeline.download.base import PaperDiscovery

        doi = "10.1234/sparse"

        async def _sparse_disc(*a, **k):
            # discovery succeeds but is missing year, journal, abstract
            return PaperDiscovery(
                doi=doi,
                title="Sparse Title",
                authors=["A. Author"],
                year=None,
                abstract=None,
                is_oa=False,
                work_type=None,
                pmcid=None,
                arxiv_id=None,
                oa_url=None,
                unpaywall_pdf_url=None,
            )

        monkeypatch.setattr(
            "perspicacite.pipeline.download.unified.discover_paper_sources", _sparse_disc
        )
        respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": {
                        "published": {"date-parts": [[2019]]},
                        "container-title": ["Journal X"],
                        "abstract": "<jats:p>Crossref abstract.</jats:p>",
                    }
                },
            )
        )
        # Europe PMC DOI search (no PMCID available; return no hit so pipeline continues)
        respx_mock.get(url__regex=r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*").mock(
            return_value=httpx.Response(200, json={"resultList": {"result": []}})
        )
        result = await retrieve_paper_content(doi)
        md = result.metadata or {}
        assert md.get("year") == 2019
        assert md.get("journal") == "Journal X"
        # title from discovery preserved (not overwritten):
        assert md.get("title") == "Sparse Title"
        # if the result ends up abstract-only, it should carry the Crossref abstract:
        if result.content_type == "abstract":
            assert result.abstract == "Crossref abstract."

    @pytest.mark.asyncio
    async def test_discovery_not_enriched_when_complete(self, respx_mock, monkeypatch):
        from perspicacite.pipeline.download import retrieve_paper_content
        from perspicacite.pipeline.download.base import PaperDiscovery

        # discovery has everything -> Crossref must NOT be called
        crossref_route = respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
            return_value=httpx.Response(
                200, json={"message": {"title": ["WRONG"], "published": {"date-parts": [[1999]]}}}
            )
        )

        async def _full_disc(*a, **k):
            return PaperDiscovery(
                doi="10.1/full",
                title="Full Title",
                authors=["X"],
                year=2021,
                abstract="A complete abstract that is long enough to be used directly here.",
                is_oa=False,
                work_type="article",
                pmcid=None,
                arxiv_id=None,
                oa_url=None,
                unpaywall_pdf_url=None,
            )

        monkeypatch.setattr(
            "perspicacite.pipeline.download.unified.discover_paper_sources", _full_disc
        )
        # Europe PMC DOI search (no PMCID available; return no hit so pipeline continues)
        respx_mock.get(url__regex=r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*").mock(
            return_value=httpx.Response(200, json={"resultList": {"result": []}})
        )
        result = await retrieve_paper_content("10.1/full")
        md = result.metadata or {}
        assert md.get("year") == 2021 and md.get("title") == "Full Title"
        assert not crossref_route.called  # no enrichment needed


class TestBioRxivInUnifiedPipeline:
    """Tests for bioRxiv/medRxiv wiring in the unified retrieval pipeline."""

    @pytest.mark.asyncio
    async def test_retrieve_paper_content_uses_biorxiv(self, respx_mock, monkeypatch):
        """When DOI is a bioRxiv DOI and discovery returns nothing, the pipeline
        should call the bioRxiv API and return its abstract."""
        doi = "10.1101/2021.01.01.425001"

        async def _empty_discovery(*a, **k):
            return PaperDiscovery(
                doi=doi,
                title=None,
                authors=[],
                year=None,
                abstract=None,
                is_oa=False,
                work_type=None,
                pmcid=None,
                arxiv_id=None,
                oa_url=None,
                unpaywall_pdf_url=None,
            )

        monkeypatch.setattr(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            _empty_discovery,
        )

        respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "messages": [{"status": "ok"}],
                    "collection": [
                        {
                            "doi": doi,
                            "title": "BR Preprint",
                            "authors": "X",
                            "date": "2021-01-01",
                            "abstract": "biorxiv abstract text",
                            "server": "biorxiv",
                            "jatsxml": "",
                        }
                    ],
                },
            )
        )
        # Europe PMC DOI search (no PMCID available; return no hit so pipeline continues)
        respx_mock.get(url__regex=r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*").mock(
            return_value=httpx.Response(200, json={"resultList": {"result": []}})
        )

        result = await retrieve_paper_content(doi)

        assert result.success
        assert result.content_source in ("biorxiv", "medrxiv")
        assert result.abstract == "biorxiv abstract text"
        assert result.content_type in ("abstract", "structured")

    @pytest.mark.asyncio
    async def test_retrieve_paper_content_skips_biorxiv_for_normal_doi(self, monkeypatch):
        """For a non-bioRxiv DOI, the pipeline must not call the bioRxiv API
        and should fall through to discovery's abstract."""

        async def _disc(*a, **k):
            return PaperDiscovery(
                doi="10.1/x",
                title="T",
                authors=["A"],
                year=2020,
                abstract="An abstract longer than twenty chars here.",
                is_oa=False,
                work_type=None,
                pmcid=None,
                arxiv_id=None,
                oa_url=None,
                unpaywall_pdf_url=None,
            )

        monkeypatch.setattr(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            _disc,
        )

        result = await retrieve_paper_content("10.1/x")

        # Must NOT route through bioRxiv; falls through to discovery abstract
        assert result.content_source != "biorxiv"
        assert result.abstract == "An abstract longer than twenty chars here."
