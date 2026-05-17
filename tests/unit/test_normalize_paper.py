"""Tests for normalize_paper_dict and Paper model normalization."""

from perspicacite.models import Author, PaperSource, normalize_paper_dict


class TestNormalizePaperDict:
    """Test the normalize_paper_dict helper function."""

    def test_openalex_format(self):
        """Test normalization from OpenAlex API response format."""
        raw = {
            "id": "https://openalex.org/W4126634999",
            "title": "Deep learning for molecular biology",
            "display_name": "Deep learning for molecular biology",
            "publication_year": 2023,
            "type": "article",
            "authorships": [
                {"author": {"display_name": "John Doe"}},
                {"author": {"display_name": "Jane Smith"}},
            ],
            "ids": {"doi": "10.1234/example.2023.001"},
            "doi": "https://doi.org/10.1234/example.2023.001",
            "abstract": "This is a test abstract.",
            "cited_by_count": 42,
        }

        result = normalize_paper_dict(raw, source=PaperSource.WEB_SEARCH)

        assert result["title"] == "Deep learning for molecular biology"
        assert result["authors"] == ["John Doe", "Jane Smith"]
        assert result["year"] == 2023
        assert result["doi"] == "10.1234/example.2023.001"
        assert result["abstract"] == "This is a test abstract."
        assert result["citation_count"] == 42
        assert result["source"] == PaperSource.WEB_SEARCH
        assert "id" in result

    def test_semantic_scholar_format(self):
        """Test normalization from Semantic Scholar format."""
        raw = {
            "paperId": "123abc",
            "title": "Test Paper Title",
            "authors": [
                {"name": "Author One", "authorId": "1"},
                {"name": "Author Two", "authorId": "2"},
                {"name": "Author Three", "authorId": "3"},
            ],
            "year": 2022,
            "abstract": "An abstract about testing.",
            "citationCount": 100,
            "doi": "10.5678/test.2022.002",
        }

        result = normalize_paper_dict(raw, source=PaperSource.SCILEX)

        assert result["title"] == "Test Paper Title"
        assert result["authors"] == ["Author One", "Author Two", "Author Three"]
        assert result["year"] == 2022
        assert result["doi"] == "10.5678/test.2022.002"
        assert result["citation_count"] == 100

    def test_author_objects(self):
        """Test normalization with Author objects."""
        raw = {
            "title": "Test with Author objects",
            "authors": [
                Author(name="First Author", given="First", family="Author"),
                Author(name="Second Author", given="Second", family="Author"),
            ],
            "year": 2024,
        }

        result = normalize_paper_dict(raw)

        assert result["authors"] == ["First Author", "Second Author"]
        assert result["year"] == 2024

    def test_author_string_list(self):
        """Test normalization with list of author name strings."""
        raw = {
            "title": "Test with string authors",
            "authors": ["Alice Smith", "Bob Jones", "Carol White"],
            "year": 2021,
        }

        result = normalize_paper_dict(raw)

        assert result["authors"] == ["Alice Smith", "Bob Jones", "Carol White"]

    def test_author_comma_separated_string(self):
        """Test normalization with comma-separated author string."""
        raw = {
            "title": "Test with comma authors",
            "authors": "Alice Smith, Bob Jones, Carol White",
            "year": 2020,
        }

        result = normalize_paper_dict(raw)

        assert result["authors"] == ["Alice Smith", "Bob Jones", "Carol White"]

    def test_author_and_separated_string(self):
        """Test normalization with ' and '-separated author string."""
        raw = {
            "title": "Test with and-separated authors",
            "authors": "Alice Smith and Bob Jones and Carol White",
            "year": 2019,
        }

        result = normalize_paper_dict(raw)

        assert result["authors"] == ["Alice Smith", "Bob Jones", "Carol White"]

    def test_no_authors(self):
        """Test normalization with missing authors."""
        raw = {
            "title": "Test with no authors",
            "year": 2023,
        }

        result = normalize_paper_dict(raw)

        assert result["authors"] == []

    def test_year_variations(self):
        """Test year extraction from various formats."""
        # Int year
        assert normalize_paper_dict({"title": "T", "year": 2023})["year"] == 2023

        # String year
        assert normalize_paper_dict({"title": "T", "year": "2023"})["year"] == 2023

        # publication_year
        assert normalize_paper_dict({"title": "T", "publication_year": 2022})["year"] == 2022

        # Year in string (extracted via regex)
        result = normalize_paper_dict({"title": "T", "year": "Published in 2023"})
        assert result["year"] == 2023

        # Boundary years (1800 is valid per Paper model)
        assert normalize_paper_dict({"title": "T", "year": 1800})["year"] == 1800
        assert normalize_paper_dict({"title": "T", "year": 1799})["year"] is None
        # Future year (current_year + 1 is valid)
        from datetime import datetime
        max_year = datetime.now().year + 1
        assert normalize_paper_dict({"title": "T", "year": max_year})["year"] == max_year
        assert normalize_paper_dict({"title": "T", "year": max_year + 1})["year"] is None

        # Invalid string
        assert normalize_paper_dict({"title": "T", "year": "invalid"})["year"] is None

    def test_doi_normalization(self):
        """Test DOI extraction and normalization."""
        # Plain DOI
        assert normalize_paper_dict({"title": "T", "doi": "10.1234/test.2023"})["doi"] == "10.1234/test.2023"

        # DOI with URL prefix
        assert normalize_paper_dict({"title": "T", "doi": "https://doi.org/10.1234/test.2023"})["doi"] == "10.1234/test.2023"

        # DOI with http prefix
        assert normalize_paper_dict({"title": "T", "doi": "http://dx.doi.org/10.1234/test.2023"})["doi"] == "10.1234/test.2023"

        # DOI with DOI: prefix
        assert normalize_paper_dict({"title": "T", "doi": "DOI:10.1234/test.2023"})["doi"] == "10.1234/test.2023"

        # Invalid DOI (no slash)
        assert normalize_paper_dict({"title": "T", "doi": "not-a-doi"})["doi"] is None

        # No DOI
        assert normalize_paper_dict({"title": "T"})["doi"] is None

    def test_id_generation(self):
        """Test ID generation from various sources."""
        # DOI-based ID
        result = normalize_paper_dict({"title": "T", "doi": "10.1234/test.2023"})
        assert result["id"] == "doi:10.1234/test.2023"

        # PMID-based ID
        result = normalize_paper_dict({"title": "T", "pmid": "12345678"})
        assert result["id"] == "pmid:12345678"

        # arXiv-based ID
        result = normalize_paper_dict({"title": "T", "arxiv_id": "2301.12345"})
        assert result["id"] == "arxiv:2301.12345"

        # Title-based generated ID (no DOI, PMID, arXiv)
        result = normalize_paper_dict({"title": "Test Paper Title"})
        assert result["id"].startswith("generated:")
        assert len(result["id"]) == len("generated:") + 12  # MD5 hash

    def test_citation_count_variations(self):
        """Test citation count extraction."""
        # Int
        assert normalize_paper_dict({"title": "T", "citation_count": 42})["citation_count"] == 42

        # String int
        assert normalize_paper_dict({"title": "T", "citation_count": "42"})["citation_count"] == 42

        # cited_by_count (OpenAlex style)
        assert normalize_paper_dict({"title": "T", "cited_by_count": 99})["citation_count"] == 99

        # Invalid string
        assert normalize_paper_dict({"title": "T", "citation_count": "invalid"})["citation_count"] is None

    def test_missing_optional_fields(self):
        """Test that missing optional fields default to None/empty."""
        raw = {"title": "Minimal Paper"}

        result = normalize_paper_dict(raw)

        assert result["title"] == "Minimal Paper"
        assert result["authors"] == []
        assert result["abstract"] is None
        assert result["year"] is None
        assert result["doi"] is None
        assert result["pmid"] is None
        assert result["url"] is None
        assert result["pdf_url"] is None
        assert result["citation_count"] is None
        assert result["full_text"] is None
        assert "metadata" in result

    def test_full_text_passed_through(self):
        """Test that full_text is preserved."""
        raw = {
            "title": "Test",
            "full_text": "This is the full paper text content.",
        }

        result = normalize_paper_dict(raw)

        assert result["full_text"] == "This is the full paper text content."

    def test_unknown_title_defaults(self):
        """Test that missing title falls back gracefully."""
        # Empty title -> "Unknown"
        assert normalize_paper_dict({})["title"] == "Unknown"

        # display_name is used if title missing
        assert normalize_paper_dict({"display_name": "From Display Name"})["title"] == "From Display Name"

    def test_authors_limited_to_10(self):
        """Test that only first 10 authors are included."""
        many_authors = [{"name": f"Author {i}"} for i in range(20)]
        raw = {"title": "Test", "authors": many_authors}

        result = normalize_paper_dict(raw)

        assert len(result["authors"]) == 10
        assert result["authors"] == ["Author 0", "Author 1", "Author 2", "Author 3", "Author 4",
                                       "Author 5", "Author 6", "Author 7", "Author 8", "Author 9"]

    def test_metadata_excludes_normalized_fields(self):
        """Test that normalized fields don't leak into metadata dict."""
        raw = {
            "title": "Test",
            "authors": ["Author One"],
            "year": 2023,
            "doi": "10.1234/test",
            "abstract": "An abstract",
            "pmid": "12345",
            "url": "https://example.com",
            "pdf_url": "https://example.com/paper.pdf",
            "citation_count": 10,
            "full_text": "Full text",
            "display_name": "Display Name",  # Should be excluded
            "publication_year": 2023,  # Should be excluded
            "custom_field": "custom_value",  # Should be in metadata
        }

        result = normalize_paper_dict(raw)

        # Check that custom field is in metadata
        assert result["metadata"]["custom_field"] == "custom_value"

        # Check that normalized fields are NOT in metadata
        assert "display_name" not in result["metadata"]
        assert "publication_year" not in result["metadata"]
        assert "title" not in result["metadata"]
        assert "authors" not in result["metadata"]

    def test_arxiv_specific_case(self):
        """Test the specific case of arXiv papers (the original bug)."""
        # Simulate OpenAlex response for arXiv paper
        raw = {
            "id": "https://openalex.org/W4392085638",
            "title": "Heterogeneous Scientific Foundation Model Collaboration",
            "display_name": "Heterogeneous Scientific Foundation Model Collaboration",
            "publication_year": 2026,
            "authorships": [
                {"author": {"display_name": "First Author"}},
                {"author": {"display_name": "Second Author"}},
                {"author": {"display_name": "Third Author"}},
            ],
            "ids": {"doi": "10.48550/arXiv.2604.27351"},
            "doi": "https://doi.org/10.48550/arXiv.2604.27351",
            "type": "article",
            "cited_by_count": 0,
        }

        result = normalize_paper_dict(raw, source=PaperSource.WEB_SEARCH)

        assert result["title"] == "Heterogeneous Scientific Foundation Model Collaboration"
        assert result["authors"] == ["First Author", "Second Author", "Third Author"]
        assert result["year"] == 2026
        assert result["doi"] == "10.48550/arXiv.2604.27351"
        assert result["citation_count"] == 0

        # Check format_references_academic can use this
        assert isinstance(result["authors"], list)
        assert all(isinstance(a, str) for a in result["authors"])
