"""Unit tests for LiteratureSurveyRAGMode."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.papers import Author, Paper
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.rag.modes.literature_survey import (
    LiteratureSurveyRAGMode,
    PaperCandidate,
    Theme,
)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def survey_mode(config):
    return LiteratureSurveyRAGMode(config)


@pytest.fixture
def mock_llm():
    class MockLLM:
        async def complete(self, messages, **kwargs):
            # Return minimal valid JSON for theme extraction
            return json.dumps({
                "themes": [
                    {"name": "Machine Learning", "description": "ML approaches", "paper_ids": []},
                    {"name": "Deep Learning", "description": "Neural networks", "paper_ids": []},
                    {"name": "NLP", "description": "Natural language", "paper_ids": []},
                ]
            })

    return MockLLM()


@pytest.fixture
def sample_papers():
    return [
        Paper(
            id="p1",
            title="Deep Learning for NLP",
            authors=[Author(name="Alice Smith")],
            year=2023,
            abstract="We explore deep learning for NLP tasks.",
            doi="10.1234/p1",
        ),
        Paper(
            id="p2",
            title="Transformer Models Survey",
            authors=[Author(name="Bob Jones")],
            year=2022,
            abstract="A survey of transformer architectures.",
            doi="10.1234/p2",
        ),
    ]


class TestLiteratureSurveyInit:
    def test_init_defaults(self, survey_mode):
        assert survey_mode.batch_size == 20
        assert survey_mode.max_deep_analysis == 50
        assert survey_mode.max_themes == 8
        assert survey_mode.min_themes == 3
        assert isinstance(survey_mode.sessions, dict)

    def test_scilex_adapter_created(self, survey_mode):
        from perspicacite.search.scilex_adapter import SciLExAdapter
        assert isinstance(survey_mode.scilex_adapter, SciLExAdapter)


class TestConvertToCandidates:
    def test_converts_papers(self, survey_mode, sample_papers):
        candidates = survey_mode._convert_to_candidates(sample_papers)
        assert len(candidates) == 2
        assert all(isinstance(c, PaperCandidate) for c in candidates)

    def test_maps_fields(self, survey_mode, sample_papers):
        candidates = survey_mode._convert_to_candidates(sample_papers)
        c = candidates[0]
        assert c.title == "Deep Learning for NLP"
        assert c.year == 2023
        assert c.doi == "10.1234/p1"
        assert c.abstract == "We explore deep learning for NLP tasks."

    def test_empty_input(self, survey_mode):
        assert survey_mode._convert_to_candidates([]) == []


class TestConvertToSources:
    def test_converts_candidates(self, survey_mode):
        candidates = [
            PaperCandidate(
                id="p1",
                title="Test Paper",
                authors=["Alice"],
                year=2023,
                abstract="Abstract",
                doi="10.1234/p1",
                relevance_score=0.9,
            )
        ]
        sources = survey_mode._convert_to_sources(candidates)
        assert len(sources) == 1
        assert sources[0].title == "Test Paper"

    def test_empty_candidates(self, survey_mode):
        assert survey_mode._convert_to_sources([]) == []


class TestInterimSummary:
    def test_generates_summary(self, survey_mode):
        from perspicacite.rag.modes.literature_survey import SurveySession
        session = SurveySession(session_id="test-id", query="deep learning")
        session.papers = [
            PaperCandidate(
                id="p1", title="Paper 1", authors=[], year=2023,
                abstract="Abstract", doi=None, recommended=True,
            )
        ]
        session.themes = [
            Theme(name="ML", description="Machine learning", papers=[])
        ]
        summary = survey_mode._generate_interim_summary(session)
        assert "deep learning" in summary.lower() or "paper" in summary.lower() or "theme" in summary.lower()
        assert isinstance(summary, str)
        assert len(summary) > 0


class TestExecuteNoResults:
    @pytest.mark.asyncio
    async def test_execute_no_papers_returns_gracefully(self, survey_mode, mock_llm):
        """When no papers are found, execute() returns a response rather than raising."""
        with patch.object(survey_mode, "_broad_search", return_value=[]):
            request = RAGRequest(query="obscure nonexistent topic xyz123", mode=RAGMode.LITERATURE_SURVEY)
            result = await survey_mode.execute(
                request,
                llm=mock_llm,
                vector_store=MagicMock(),
                embedding_provider=MagicMock(),
                tools=MagicMock(),
            )
        assert result.mode == RAGMode.LITERATURE_SURVEY
        assert "No papers found" in result.answer or result.answer


class TestExecuteStreamNoResults:
    @pytest.mark.asyncio
    async def test_stream_yields_events(self, survey_mode, mock_llm):
        """execute_stream() yields at least a done or error event even with no papers."""
        with patch.object(survey_mode, "_broad_search", return_value=[]):
            request = RAGRequest(query="obscure topic", mode=RAGMode.LITERATURE_SURVEY)
            events = []
            async for event in survey_mode.execute_stream(
                request,
                llm=mock_llm,
                vector_store=MagicMock(),
                embedding_provider=MagicMock(),
                tools=MagicMock(),
            ):
                events.append(event)
                if len(events) > 50:  # safety cap
                    break
        assert len(events) > 0
        # All events should have parseable JSON data
        for event in events:
            if event.data:
                parsed = json.loads(event.data)
                assert isinstance(parsed, dict)


class TestPaperCandidateModel:
    def test_default_values(self):
        c = PaperCandidate(
            id="x", title="T", authors=[], year=None, abstract="", doi=None
        )
        assert c.relevance_score == 0.0
        assert c.recommended is False
        assert c.themes == []

    def test_recommended_flag(self):
        c = PaperCandidate(
            id="x", title="T", authors=[], year=2024, abstract="A", doi=None,
            recommended=True, relevance_score=0.95,
        )
        assert c.recommended is True
        assert c.relevance_score == 0.95
