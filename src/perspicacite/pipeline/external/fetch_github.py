"""Synced from AgenticScienceBuilder @ a10eced — httpx-adapted, keep API in sync.

GitHub fetch helpers: README + (optional) extra docs / env files /
notebooks / tree / data-manifest. Notebook outputs stripped via
``pipeline.external.notebooks.strip_notebook_outputs``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.external.http import (
    http_get_bytes,
    http_get_json,
    http_get_text,
)
from perspicacite.pipeline.external.notebooks import strip_notebook_outputs

logger = get_logger("perspicacite.external.github")

GITHUB_DOC_EXTS = (".md", ".txt", ".rst")
GITHUB_DOC_SUBDIRS = ("docs", "doc", "documentation", "vignettes", "tutorials", "articles")
GITHUB_NOTEBOOK_SUBDIRS = ("notebooks", "notebook", "examples", "tutorials", "demos", "scripts")
GITHUB_DATA_SUBDIRS = ("data", "input", "inputs", "output", "outputs", "results", "dataset", "datasets")
GITHUB_ENV_FILENAMES = frozenset({
    "requirements.txt", "requirements-dev.txt", "requirements_dev.txt",
    "requirements-test.txt", "requirements_test.txt",
    "pyproject.toml", "setup.cfg", "setup.py",
    "environment.yml", "environment.yaml",
    "Pipfile", "Pipfile.lock",
    "renv.lock", "DESCRIPTION",
    "Makefile", "CMakeLists.txt",
    "conda.yml", "conda.yaml",
})
GITHUB_DOC_MAX_FILES = 15
GITHUB_DOC_MAX_BYTES_EACH = 80_000
GITHUB_DOC_RUN_BUDGET = 300_000
GITHUB_SCRIPT_EXTS = (".py", ".R", ".r", ".jl")


def _auth_headers(token: str | None) -> dict[str, str]:
    tok = token or os.environ.get("GITHUB_TOKEN") or None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


async def fetch_github_repo(
    full_name: str, *,
    cache_dir: Path,
    ttl_seconds: int = 30 * 86400,
    token: str | None = None,
) -> dict[str, Any] | None:
    """Fetch ``/repos/<full_name>`` metadata. Returns the raw JSON or None."""
    url = f"https://api.github.com/repos/{full_name}"
    return await http_get_json(
        url, cache_dir=cache_dir, api="github", query=full_name,
        ttl_seconds=ttl_seconds, headers=_auth_headers(token),
    )


async def fetch_github_docs(
    owner: str, repo: str, *,
    capsule_dir: Path,
    cache_dir: Path,
    text_file_extensions: list[str] | tuple[str, ...] | None = None,
    max_bytes_per_file: int = GITHUB_DOC_MAX_BYTES_EACH,
    extra_docs: bool = True,
    ttl_seconds: int = 30 * 86400,
    token: str | None = None,
) -> dict[str, Any]:
    """Fetch README + optional docs/notebooks/scripts/env/tree into capsule.

    Writes under ``capsule_dir/external/github/<owner>__<repo>/`` with
    subdirs ``docs/``, ``env/``, ``notebooks/``, ``scripts/`` plus
    ``tree.json`` and ``data_manifest.json``.

    Returns ``{"files_fetched": int, "bytes_fetched": int, "paths": [...]}``.
    """
    full = f"{owner}/{repo}"
    base = capsule_dir / "external" / "github" / f"{owner}__{repo}"
    base.mkdir(parents=True, exist_ok=True)
    sentinel = base / ".extra_fetched"

    summary: dict[str, Any] = {"files_fetched": 0, "bytes_fetched": 0, "paths": []}

    # README via the readme API endpoint (returns raw via Accept header).
    readme_path = base / "README.md"
    if not readme_path.exists():
        raw = await http_get_text(
            f"https://api.github.com/repos/{full}/readme",
            cache_dir=cache_dir, api="github_readme", query=full,
            ttl_seconds=ttl_seconds,
            headers={**_auth_headers(token), "Accept": "application/vnd.github.raw"},
            max_bytes=max_bytes_per_file,
        )
        if raw is not None:
            readme_path.write_text(raw, encoding="utf-8")
            summary["files_fetched"] += 1
            summary["bytes_fetched"] += len(raw.encode("utf-8"))
            summary["paths"].append("README.md")

    if not extra_docs or sentinel.exists():
        return summary

    # Trees API listing (recursive).
    tree_json = await http_get_text(
        f"https://api.github.com/repos/{full}/git/trees/HEAD?recursive=1",
        cache_dir=cache_dir, api="github_tree", query=full,
        ttl_seconds=ttl_seconds, headers=_auth_headers(token),
    )
    if tree_json is None:
        return summary
    (base / "tree.json").write_text(tree_json, encoding="utf-8")
    try:
        tree_data = json.loads(tree_json)
    except json.JSONDecodeError:
        tree_data = {}

    docs_dir = base / "docs"
    env_dir = base / "env"
    notebooks_dir = base / "notebooks"
    scripts_dir = base / "scripts"
    for d in (docs_dir, env_dir, notebooks_dir, scripts_dir):
        d.mkdir(exist_ok=True)

    doc_fetched = 0
    run_bytes = 0
    data_paths: list[dict] = []
    allowed_exts = set(text_file_extensions or [])

    for item in tree_data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        parts = path.split("/")
        filename = parts[-1]
        top = parts[0] if parts else ""

        # Data dirs — manifest only, no blob.
        if top in GITHUB_DATA_SUBDIRS:
            data_paths.append({"path": path, "size": item.get("size")})
            continue

        # Env files at root.
        if len(parts) == 1 and filename in GITHUB_ENV_FILENAMES:
            target = env_dir / filename
            if target.exists():
                continue
            blob_text = await _fetch_blob_text(
                full, path, item.get("sha"), cache_dir=cache_dir,
                ttl_seconds=ttl_seconds, token=token,
                max_bytes=max_bytes_per_file,
            )
            if blob_text is not None and run_bytes + len(blob_text.encode()) <= GITHUB_DOC_RUN_BUDGET:
                target.write_text(blob_text, encoding="utf-8")
                summary["files_fetched"] += 1
                summary["bytes_fetched"] += len(blob_text.encode("utf-8"))
                summary["paths"].append(f"env/{filename}")
                run_bytes += len(blob_text.encode("utf-8"))
            continue

        # Doc files under doc subdirs.
        if top in GITHUB_DOC_SUBDIRS and any(filename.endswith(e) for e in GITHUB_DOC_EXTS):
            if doc_fetched >= GITHUB_DOC_MAX_FILES:
                continue
            target = docs_dir / "__".join(parts[1:])
            if target.exists():
                continue
            blob_text = await _fetch_blob_text(
                full, path, item.get("sha"), cache_dir=cache_dir,
                ttl_seconds=ttl_seconds, token=token,
                max_bytes=max_bytes_per_file,
            )
            if blob_text is None:
                continue
            if run_bytes + len(blob_text.encode()) > GITHUB_DOC_RUN_BUDGET:
                continue
            target.write_text(blob_text, encoding="utf-8")
            doc_fetched += 1
            summary["files_fetched"] += 1
            summary["bytes_fetched"] += len(blob_text.encode("utf-8"))
            summary["paths"].append(f"docs/{target.name}")
            run_bytes += len(blob_text.encode("utf-8"))
            continue

        # Notebooks and scripts.
        if top in GITHUB_NOTEBOOK_SUBDIRS:
            if filename.endswith(".ipynb"):
                target = notebooks_dir / "__".join(parts[1:])
                if target.exists():
                    continue
                blob_text = await _fetch_blob_text(
                    full, path, item.get("sha"), cache_dir=cache_dir,
                    ttl_seconds=ttl_seconds, token=token,
                    max_bytes=max_bytes_per_file * 4,
                )
                if blob_text is None:
                    continue
                stripped = strip_notebook_outputs(blob_text)
                if run_bytes + len(stripped.encode()) > GITHUB_DOC_RUN_BUDGET:
                    continue
                target.write_text(stripped, encoding="utf-8")
                summary["files_fetched"] += 1
                summary["bytes_fetched"] += len(stripped.encode("utf-8"))
                summary["paths"].append(f"notebooks/{target.name}")
                run_bytes += len(stripped.encode("utf-8"))
                continue
            # Scripts: .py/.R/.r/.jl (subject to allowlist for ext-widening).
            if any(filename.endswith(e) for e in GITHUB_SCRIPT_EXTS):
                if allowed_exts and not any(filename.endswith(e) for e in allowed_exts):
                    continue
                target = scripts_dir / "__".join(parts[1:])
                if target.exists():
                    continue
                blob_text = await _fetch_blob_text(
                    full, path, item.get("sha"), cache_dir=cache_dir,
                    ttl_seconds=ttl_seconds, token=token,
                    max_bytes=max_bytes_per_file,
                )
                if blob_text is None:
                    continue
                if run_bytes + len(blob_text.encode()) > GITHUB_DOC_RUN_BUDGET:
                    continue
                target.write_text(blob_text, encoding="utf-8")
                summary["files_fetched"] += 1
                summary["bytes_fetched"] += len(blob_text.encode("utf-8"))
                summary["paths"].append(f"scripts/{target.name}")
                run_bytes += len(blob_text.encode("utf-8"))

    if data_paths:
        (base / "data_manifest.json").write_text(
            json.dumps(data_paths, indent=2), encoding="utf-8"
        )
        summary["paths"].append("data_manifest.json")

    sentinel.touch()
    return summary


async def _fetch_blob_text(
    full: str, path: str, sha: str | None, *,
    cache_dir: Path, ttl_seconds: int, token: str | None,
    max_bytes: int,
) -> str | None:
    """Fetch a single blob as text via the contents API (raw)."""
    url = f"https://api.github.com/repos/{full}/contents/{path}"
    headers = {**_auth_headers(token), "Accept": "application/vnd.github.raw"}
    return await http_get_text(
        url, cache_dir=cache_dir, api="github_blob",
        query=f"{full}:{path}",
        ttl_seconds=ttl_seconds, headers=headers, max_bytes=max_bytes,
    )
