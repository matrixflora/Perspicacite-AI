"""Per-backend failure isolation tests for SciLExAdapter.

When one backend fails (raises), the aggregator must log loudly and
return the successful backends' results — not poison the whole fan-out
with zero. This is the structural part of the 2026-05-15 Scriptorium-
integration A3 fix.
"""

import logging
from unittest.mock import MagicMock

from perspicacite.search.scilex_adapter import SciLExAdapter


class TestCollectFromBackend:
    """Unit-test the per-backend helper directly. No SciLEx mocking
    needed — _collect_from_backend takes the collector and lists as
    arguments, so we can stub them with a MagicMock."""

    def _make_adapter(self):
        # Constructing SciLExAdapter does a soft import; we bypass
        # availability for the helper test by setting the flag manually.
        adapter = SciLExAdapter()
        adapter._scilex_available = True
        return adapter

    def test_successful_backend_appended_to_success_list(self, caplog):
        adapter = self._make_adapter()
        collector = MagicMock()
        successful: list[str] = []
        failed: list[str] = []

        with caplog.at_level(logging.INFO, logger="perspicacite.search.scilex"):
            adapter._collect_from_backend(
                collector=collector,
                api_name="SemanticScholar",
                api_collect_list=[{"api": "SemanticScholar"}],
                successful_backends=successful,
                failed_backends=failed,
            )

        collector.run_job_collects.assert_called_once_with(
            [{"api": "SemanticScholar"}]
        )
        assert successful == ["SemanticScholar"]
        assert failed == []

    def test_failing_backend_logs_warning_and_appends_to_failed(self, capsys):
        adapter = self._make_adapter()
        collector = MagicMock()
        collector.run_job_collects.side_effect = RuntimeError(
            "OpenAlex network timeout (simulated)"
        )
        successful: list[str] = []
        failed: list[str] = []

        adapter._collect_from_backend(
            collector=collector,
            api_name="OpenAlex",
            api_collect_list=[{"api": "OpenAlex"}],
            successful_backends=successful,
            failed_backends=failed,
        )

        # Failure isolated — no raise
        assert successful == []
        assert failed == ["OpenAlex"]

        # Loud warning emitted with backend name.
        # structlog uses PrintLoggerFactory in test environments (no
        # setup_logging called), so output goes to stdout — check there.
        captured = capsys.readouterr()
        log_output = captured.out + captured.err
        assert "OpenAlex" in log_output, (
            "Expected a WARNING log mentioning OpenAlex in stdout/stderr; "
            f"got: {log_output!r}"
        )
        assert "warning" in log_output.lower() or "WARNING" in log_output or "scilex_backend_failed" in log_output, (
            "Expected log output to indicate a warning/failure; "
            f"got: {log_output!r}"
        )

    def test_failure_does_not_raise(self):
        """The helper must swallow exceptions — the parent loop relies on
        this to continue with other backends."""
        adapter = self._make_adapter()
        collector = MagicMock()
        collector.run_job_collects.side_effect = ValueError("bad")
        # Should NOT raise
        adapter._collect_from_backend(
            collector=collector,
            api_name="X",
            api_collect_list=[],
            successful_backends=[],
            failed_backends=[],
        )


class TestAllBackendsFailReturnEmpty:
    """When every backend fails (and so no search dir is created),
    _scilex_search_sync must return [] rather than raising FileNotFoundError
    on the Phase 2 iterdir."""

    def test_phase_2_no_results_dir_returns_empty(self, monkeypatch, tmp_path, caplog):
        """Mock the SciLEx imports so Phase 1 runs but writes no files.
        Phase 2 should then hit the 'no results dir' branch and return []."""
        adapter = SciLExAdapter()
        adapter._scilex_available = True

        # Stub the SciLEx import in a minimal way — return a collector that
        # mutates nothing on the filesystem.
        fake_collector = MagicMock()
        fake_collector.queryCompositor.return_value = {
            "SemanticScholar": [{"q": "x"}]
        }
        # run_job_collects raises → backend marked failed, no files written
        fake_collector.run_job_collects.side_effect = RuntimeError("dead")

        import sys
        scilex_pkg = MagicMock()
        cc_mod = MagicMock()
        cc_mod.CollectCollection = MagicMock(return_value=fake_collector)
        agg_mod = MagicMock()
        for name in (
            "OpenAlextoZoteroFormat", "SemanticScholartoZoteroFormat",
            "ArxivtoZoteroFormat", "PubMedtoZoteroFormat",
            "IEEEtoZoteroFormat", "SpringertoZoteroFormat",
            "DBLPtoZoteroFormat",
        ):
            setattr(agg_mod, name, MagicMock())
        agg_mod.deduplicate = lambda df: df
        monkeypatch.setitem(sys.modules, "scilex", scilex_pkg)
        monkeypatch.setitem(sys.modules, "scilex.crawlers", MagicMock())
        monkeypatch.setitem(sys.modules, "scilex.crawlers.collector_collection", cc_mod)
        monkeypatch.setitem(sys.modules, "scilex.crawlers.aggregate", agg_mod)

        with caplog.at_level(logging.WARNING, logger="perspicacite.search.scilex"):
            result = adapter._scilex_search_sync(
                query="anything",
                max_results=10,
                year_min=None,
                year_max=None,
                apis=["semantic_scholar"],
            )

        assert result == [], (
            f"All-backends-fail should return [], got {result}. "
            "Phase 2 must short-circuit when the results dir is missing."
        )
