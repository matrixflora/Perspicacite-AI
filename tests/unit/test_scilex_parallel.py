"""Per-backend fan-out should be concurrent (ThreadPoolExecutor), not
serial. A failure in one backend must not delay or poison the others."""
import time
from unittest.mock import MagicMock

from perspicacite.search.scilex_adapter import SciLExAdapter


class TestParallelFanOut:
    """Verify _collect_all_backends fans out per-backend collection
    concurrently rather than serially."""

    def test_per_backend_collection_runs_concurrently(self):
        """Three slow (0.3s) backends should complete in ~0.3s parallel,
        not ~0.9s serial."""
        adapter = SciLExAdapter()
        adapter._scilex_available = True

        # Stub collector: each run_job_collects call sleeps 0.3s
        collector = MagicMock()

        def slow_collect(api_collect_list):
            time.sleep(0.3)

        collector.run_job_collects.side_effect = slow_collect

        queries_by_api = {
            "SemanticScholar": [{"q": "x"}],
            "OpenAlex": [{"q": "x"}],
            "PubMed": [{"q": "x"}],
        }

        # The helper we'll add wraps the per-backend dispatch loop
        t0 = time.monotonic()
        successful, failed = adapter._collect_all_backends(
            collector=collector,
            queries_by_api=queries_by_api,
            max_results=10,
        )
        elapsed = time.monotonic() - t0

        assert elapsed < 0.7, (
            f"Fan-out appears serial — took {elapsed:.2f}s for 3x0.3s backends. "
            "Expected ~0.3-0.5s parallel."
        )
        assert sorted(successful) == sorted(queries_by_api.keys())
        assert failed == []

    def test_per_backend_failure_does_not_block_or_poison_others(self):
        """If one backend raises, the others still complete and we get
        partial success."""
        adapter = SciLExAdapter()
        adapter._scilex_available = True

        collector = MagicMock()

        def flaky_collect(api_collect_list):
            api = api_collect_list[0]["api"]
            if api == "OpenAlex":
                raise RuntimeError("openalex flaked")

        collector.run_job_collects.side_effect = flaky_collect

        queries_by_api = {
            "SemanticScholar": [{"q": "x"}],
            "OpenAlex": [{"q": "x"}],
            "PubMed": [{"q": "x"}],
        }

        successful, failed = adapter._collect_all_backends(
            collector=collector,
            queries_by_api=queries_by_api,
            max_results=10,
        )

        assert "OpenAlex" in failed
        assert "SemanticScholar" in successful
        assert "PubMed" in successful


class TestCollectAllBackendsPreservesQueryStructure:
    """Make sure the new helper still builds api_collect_list correctly
    (max_articles_per_query, output_dir, api fields)."""

    def test_each_backend_receives_proper_query_dict(self):
        adapter = SciLExAdapter()
        adapter._scilex_available = True

        collector = MagicMock()
        captured: list[list] = []

        def capture(api_collect_list):
            captured.append(list(api_collect_list))

        collector.run_job_collects.side_effect = capture

        queries_by_api = {
            "SemanticScholar": [{"q": "x"}],
        }

        adapter._collect_all_backends(
            collector=collector,
            queries_by_api=queries_by_api,
            max_results=7,
        )

        assert len(captured) == 1
        items = captured[0]
        assert len(items) == 1
        item = items[0]
        assert item["api"] == "SemanticScholar"
        assert item["query"]["q"] == "x"
        # max_articles_per_query should be 2x max_results per the existing pattern
        assert item["query"]["max_articles_per_query"] == 14
