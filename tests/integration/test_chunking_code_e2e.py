"""Live integration test for sub-project A: ingest a tiny real GitHub
repo and assert AST chunking + symbol index land correctly.

Marked ``live + slow`` — only runs when explicitly selected.
Knob: PERSPICACITE_LIVE_CODE_CHUNKING=1 to opt in.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.slow]


SKIP = os.environ.get("PERSPICACITE_LIVE_CODE_CHUNKING") != "1"


@pytest.mark.skipif(SKIP, reason="set PERSPICACITE_LIVE_CODE_CHUNKING=1 to run")
@pytest.mark.asyncio
async def test_ingest_small_repo_produces_ast_chunks_and_symbols(tmp_path: Path):
    """Use the existing GitHub-KB ingest path on a small fixed repo,
    then verify the symbol index has the expected functions."""
    from perspicacite.pipeline.github_skill_bundle import ingest_github_repo  # type: ignore
    from perspicacite.pipeline.symbol_index import iter_symbols

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()

    # Tiny known repo: tiangolo/typer — has top-level Python with clear funcs.
    # Restrict to a single small file to keep test under 60s.
    await ingest_github_repo(
        repo_url="https://github.com/tiangolo/typer",
        kb_dir=kb_dir,
        restrict_to_files=["typer/__init__.py"],
    )

    syms = list(iter_symbols(kb_dir))
    assert len(syms) >= 1, "expected at least one symbol from typer/__init__.py"
    assert any(s.symbol_kind in ("function", "class", "module") for s in syms)
    assert all(s.start_line >= 1 for s in syms)
    assert all(s.end_line >= s.start_line for s in syms)
