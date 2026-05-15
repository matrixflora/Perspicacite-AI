# Institutional PDF Access via Browser Cookies

Papers behind a publisher paywall that your institution licenses are reachable by
replaying the session cookies your browser holds after you have authenticated through
your library proxy or publisher SSO. This is the same mechanism the Zotero Connector
browser extension uses, applied server-side.

---

## Prerequisites

- You are logged in to your institutional proxy or directly to publisher sites in
  your browser
- The `cookies` extra is installed: `uv pip install -e ".[cookies]"`
- Supported browsers: Chrome, Brave, Firefox, Edge, Safari, Opera, Arc

---

## Step 1: Export your browser cookies

```bash
perspicacite import-browser-cookies \
  --browser brave \
  --domain nature.com \
  --domain wiley.com \
  --domain sciencedirect.com \
  --domain pubs.acs.org \
  --output ~/.config/perspicacite/cookies.txt
```

The command:
1. Reads and decrypts the browser's cookie store (using the OS keychain on macOS —
   you may see a permission prompt)
2. Filters to cookies matching the specified domains
3. Writes a Netscape-format `cookies.txt` with `chmod 600`
4. Prints the matching `config.yml` block to paste

Typical output:

```
Wrote 54 of 3122 cookies to /Users/you/.config/perspicacite/cookies.txt
Top cookie hosts captured:
   18  www.nature.com
   12  onlinelibrary.wiley.com
    9  sciencedirect.com
    7  pubs.acs.org

Add to your config.yml:

pdf_download:
  cookies_path: "/Users/you/.config/perspicacite/cookies.txt"
  cookie_domains:
    - "nature.com"
    - "onlinelibrary.wiley.com"
    - "sciencedirect.com"
    - "pubs.acs.org"
```

## Step 2: Update config.yml

Paste the block from the output into `config.yml` under `pdf_download:`:

```yaml
pdf_download:
  unpaywall_email: "your@email.com"
  cookies_path: "/Users/you/.config/perspicacite/cookies.txt"
  cookie_domains:
    - "nature.com"
    - "onlinelibrary.wiley.com"
    - "sciencedirect.com"
    - "pubs.acs.org"
```

If `cookie_domains` is an empty list (`[]`), cookies are attached to all PDF
requests — broadest access, but slight risk of cookie leakage to third-party hosts
in redirect chains. Listing specific domains is safer.

## Step 3: Restart and verify

```bash
# Restart the server to pick up the new config
uv run perspicacite -c config.yml serve

# Test a paywalled DOI and check for a successful PDF download:
curl -X POST http://localhost:5468/api/kb/my-kb/dois/async \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/s41586-023-06924-6"]}'

curl -sN "http://localhost:5468/api/jobs/<job_id>/events"
# Look for: {"pdf_download": {"attempted": 1, "success": 1, "failed": 0}}
```

---

## Checking cookie freshness

Session cookies expire. Run `check-cookies` to see which domains are still valid:

```bash
perspicacite check-cookies
```

Example output:

```
Cookie freshness for /Users/you/.config/perspicacite/cookies.txt
  DOMAIN                     STATUS           HOSTS  EXPIRES
  nature.com                 ok                   3  2026-08-04
  onlinelibrary.wiley.com    ok                   2  2026-10-07
  sciencedirect.com          expiring_soon        1  2026-05-22
  pubs.acs.org               all_expired          1  —
```

Domains flagged `expiring_soon` (≤ 7 days) or `all_expired` need re-export.
The command exits non-zero when any domain has expired cookies, making it safe to
wire into a daily cron:

```bash
# Example cron: check daily at 8am, re-export if any domain is expired
0 8 * * * perspicacite check-cookies || perspicacite import-browser-cookies \
    --browser brave --domain nature.com --domain wiley.com \
    --output ~/.config/perspicacite/cookies.txt
```

The PDF downloader also logs a `pdf_cookie_likely_expired` warning when a publisher
returns an HTML response on a cookie-gated URL — the canonical symptom of a stale
institutional cookie.

---

## Manual cookie export

If the automated export does not work for your browser or platform, you can use a
browser extension ("Get cookies.txt LOCALLY", "EditThisCookie", etc.) to export a
Netscape-format `cookies.txt`. Drop it at the `cookies_path` configured in
`config.yml` — Perspicacité picks it up the same way.

---

## Security notes

- The cookies file is written `chmod 600` — anyone with read access to the file can
  impersonate your library session.
- Only cookies for the listed `cookie_domains` are exported. Do not put credentials
  in the file path (use environment variables or OS keychain instead).
- Institutional cookies authorize access that your institution pays for. Do not share
  the cookies file with people outside your institution.

---

## Related topics

- [guides/ingest-bibtex.md](ingest-bibtex.md) — the pipeline that uses these cookies
  during ingest
- [reference/cli.md](../reference/cli.md) — `import-browser-cookies` and
  `check-cookies` flags
- [reference/config.md](../reference/config.md) — `pdf_download.*` config keys
