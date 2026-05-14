"""Cookie-jar freshness inspection.

Institutional / paywall PDF fetch relies on a Netscape ``cookies.txt``
exported from the user's logged-in browser. Those cookies expire — some
in days, some in months — and the failure mode (cookie silently expired,
publisher serves the paywall HTML in place of the PDF) is confusing.

This module gives us:

- :func:`scan_cookie_freshness` — per-host summary of expired / expiring /
  session-only / fresh cookies, computed from the ``http.cookiejar``
  ``expires`` field. Pure inspection, no network.
- :func:`check_cookie_freshness_for_domains` — given the user's
  ``pdf_download.cookie_domains`` allowlist, produce a list of warnings
  ready to print (CLI) or log (downloader startup).

The downloader calls the second helper once at jar-load time and
surfaces stale entries via :mod:`perspicacite.logging`. The CLI
``check-cookies`` subcommand prints the same report for the user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

# 7 days = soon enough that we should nag the user to re-import
EXPIRY_SOON_SECONDS = 7 * 24 * 3600


@dataclass
class HostCookieSummary:
    """One row of the freshness report.

    ``fresh_max_expiry`` is None when this host only has expired or
    session cookies; otherwise it's the latest still-valid Unix
    timestamp. We use it both to rank "is this host healthy" and to
    show a human-readable expiry date.
    """

    host: str
    total: int = 0
    expired: int = 0
    expiring_soon: int = 0
    session: int = 0
    fresh_max_expiry: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.fresh_max_expiry is not None:
            d["fresh_max_expiry_iso"] = datetime.fromtimestamp(
                self.fresh_max_expiry, tz=timezone.utc
            ).isoformat()
        return d


def scan_cookie_freshness(jar: Iterable[Any]) -> dict[str, HostCookieSummary]:
    """Categorize every cookie in ``jar`` by host and expiry state.

    ``jar`` is any iterable of cookie objects with ``.domain`` and
    ``.expires`` (None for session cookies); :class:`http.cookiejar.Cookie`
    fits, which is what :class:`http.cookiejar.MozillaCookieJar` yields.
    """
    now = int(time.time())
    soon = now + EXPIRY_SOON_SECONDS
    by_host: dict[str, HostCookieSummary] = {}
    for c in jar:
        host = (getattr(c, "domain", "") or "").lstrip(".").lower()
        if not host:
            continue
        entry = by_host.setdefault(host, HostCookieSummary(host=host))
        entry.total += 1
        exp = getattr(c, "expires", None)
        if exp is None or getattr(c, "discard", False):
            entry.session += 1
            continue
        try:
            exp_i = int(exp)
        except (TypeError, ValueError):
            entry.session += 1
            continue
        if exp_i < now:
            entry.expired += 1
        elif exp_i < soon:
            entry.expiring_soon += 1
            if entry.fresh_max_expiry is None or exp_i > entry.fresh_max_expiry:
                entry.fresh_max_expiry = exp_i
        else:
            if entry.fresh_max_expiry is None or exp_i > entry.fresh_max_expiry:
                entry.fresh_max_expiry = exp_i
    return by_host


@dataclass
class CookieDomainWarning:
    """One entry in the freshness report for a configured domain.

    ``status`` is one of: ``"no_cookies"``, ``"all_expired"``,
    ``"expiring_soon"``, ``"ok"``.
    """

    domain: str
    status: str
    matched_hosts: int = 0
    soonest_expiry: int | None = None
    advice: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.soonest_expiry is not None:
            d["soonest_expiry_iso"] = datetime.fromtimestamp(
                self.soonest_expiry, tz=timezone.utc
            ).isoformat()
        return d


def check_cookie_freshness_for_domains(
    jar: Iterable[Any],
    cookie_domains: list[str],
) -> list[CookieDomainWarning]:
    """For each ``cookie_domains`` substring, classify its overall health.

    Returns one :class:`CookieDomainWarning` per configured domain.
    Callers typically filter to ``status != "ok"`` for the warning
    surface and show all entries in the ``check-cookies`` report.
    """
    if not cookie_domains:
        return []
    summary = scan_cookie_freshness(jar)
    results: list[CookieDomainWarning] = []
    now = int(time.time())
    soon = now + EXPIRY_SOON_SECONDS
    for d in cookie_domains:
        d_lower = d.lower()
        matching = [s for h, s in summary.items() if d_lower in h]
        if not matching:
            results.append(
                CookieDomainWarning(
                    domain=d,
                    status="no_cookies",
                    matched_hosts=0,
                    advice=(
                        "No cookies captured for this domain. The browser "
                        "may not be logged in to it, or the host substring "
                        "doesn't match. Re-run `perspicacite "
                        "import-browser-cookies`."
                    ),
                )
            )
            continue
        fresh_expiries = [
            s.fresh_max_expiry for s in matching if s.fresh_max_expiry is not None
        ]
        if not fresh_expiries:
            results.append(
                CookieDomainWarning(
                    domain=d,
                    status="all_expired",
                    matched_hosts=len(matching),
                    advice=(
                        "All cookies for this domain are expired or "
                        "session-only. Re-run `perspicacite "
                        "import-browser-cookies` after logging in again."
                    ),
                )
            )
            continue
        soonest = min(fresh_expiries)
        if soonest < soon:
            results.append(
                CookieDomainWarning(
                    domain=d,
                    status="expiring_soon",
                    matched_hosts=len(matching),
                    soonest_expiry=soonest,
                    advice=(
                        "Cookies expire within 7 days. Re-run "
                        "`perspicacite import-browser-cookies` soon."
                    ),
                )
            )
        else:
            results.append(
                CookieDomainWarning(
                    domain=d,
                    status="ok",
                    matched_hosts=len(matching),
                    soonest_expiry=soonest,
                )
            )
    return results


def looks_like_paywall_html(content: bytes, *, head: int = 2048) -> bool:
    """Cheap heuristic: did the publisher return HTML instead of a PDF?

    Used in the downloader to distinguish "PDF body" from "HTML landing
    page" — the latter is the canonical symptom of an expired or missing
    cookie on a paywalled article. We only need to be right enough to
    tell the user "your cookies probably need refreshing".
    """
    if not content:
        return False
    head_b = content[:head].lower()
    return b"<html" in head_b or b"<!doctype html" in head_b
