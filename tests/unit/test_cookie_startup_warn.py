"""Tests for the startup cookie-freshness warning (Priority 7)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from perspicacite.web.state import _warn_stale_cookies


def _write_cookie_jar(path: Path, lines: list[str]) -> None:
    path.write_text("# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n")


def test_warn_skipped_when_no_cookies_path_or_domains(caplog):
    """No-op when either input is unset — common at server boot in tests."""
    with caplog.at_level(logging.WARNING):
        _warn_stale_cookies(cookies_path=None, cookie_domains=["nature.com"])
        _warn_stale_cookies(cookies_path="/tmp/foo", cookie_domains=[])
    assert "pdf_cookies" not in caplog.text


def test_warn_logs_when_cookie_file_missing(caplog, tmp_path):
    missing = tmp_path / "nope.txt"
    with caplog.at_level(logging.WARNING):
        _warn_stale_cookies(cookies_path=str(missing), cookie_domains=["nature.com"])
    assert "pdf_cookies_missing" in caplog.text


def test_warn_logs_one_per_stale_domain(caplog, tmp_path):
    """A jar with no cookies for the configured domains → one warning per."""
    jar_path = tmp_path / "cookies.txt"
    _write_cookie_jar(jar_path, [
        # one valid cookie for an unrelated domain
        f".example.com\tTRUE\t/\tFALSE\t{int(time.time()) + 86400}\tk\tv",
    ])
    with caplog.at_level(logging.WARNING):
        _warn_stale_cookies(
            cookies_path=str(jar_path),
            cookie_domains=["nature.com", "pubs.acs.org"],
        )
    text = caplog.text
    assert "pdf_cookies_stale" in text
    # both domains get warnings (status: no_cookies)
    assert text.count("pdf_cookies_stale") == 2


def test_no_warn_when_all_cookies_fresh(caplog, tmp_path):
    """All configured domains have fresh non-session cookies → no warnings,
    just an info log."""
    jar_path = tmp_path / "cookies.txt"
    future_ts = int(time.time()) + 86400 * 30  # +30d
    _write_cookie_jar(jar_path, [
        f".nature.com\tTRUE\t/\tFALSE\t{future_ts}\tk\tv",
        f".pubs.acs.org\tTRUE\t/\tFALSE\t{future_ts}\tk\tv",
    ])
    with caplog.at_level(logging.INFO):
        _warn_stale_cookies(
            cookies_path=str(jar_path),
            cookie_domains=["nature.com", "pubs.acs.org"],
        )
    assert "pdf_cookies_health_ok" in caplog.text
    assert "pdf_cookies_stale" not in caplog.text
