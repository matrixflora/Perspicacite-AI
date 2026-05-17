"""Produce Paper fixtures from a repository directory.

For v1 these content types are handled:
- Markdown / RST -> single Paper per file, full text
- Python modules -> docstrings + function/class signatures (NOT full source)
- Jupyter notebooks -> markdown cells + code cell signatures, no outputs
"""
from __future__ import annotations

import ast
from pathlib import Path

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.github.bundle import BundleManifest, extract_links_from_text
from perspicacite.pipeline.github.walk import walk_filtered

logger = get_logger("perspicacite.pipeline.github.chunk_producer")


def _extract_py_docstrings(source: str) -> str:
    """Extract module, class, and function docstrings + signatures from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source[:2000]  # fall back to truncated source

    lines: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        lines.append(module_doc)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node)
            sig = node.name
            if isinstance(node, ast.ClassDef):
                sig = f"class {node.name}"
            else:
                # Build a minimal signature
                args = [a.arg for a in node.args.args]
                sig = f"def {node.name}({', '.join(args)})"
            if doc:
                lines.append(f"{sig}:\n    \"\"\"{doc}\"\"\"")
            else:
                lines.append(sig)

    return "\n\n".join(lines)


def _strip_notebook(content: str) -> str:
    """Strip a Jupyter notebook to markdown cells + code cell lines (no outputs)."""
    try:
        import nbformat
        nb = nbformat.reads(content, as_version=4)
        parts: list[str] = []
        for i, cell in enumerate(nb.cells):
            if cell.cell_type == "markdown":
                parts.append(cell.source)
            elif cell.cell_type == "code":
                # Include code but drop large base64 outputs
                code = cell.source
                if len(code) < 10000:
                    parts.append(f"# Cell {i + 1}\n{code}")
        return "\n\n".join(parts)
    except Exception:
        return content[:5000]


def papers_from_directory(
    root: Path,
    manifest: BundleManifest,
    commit_sha: str,
    *,
    max_file_bytes: int = 200_000,
) -> list[Paper]:
    """Walk ``root``, filter by manifest.content globs, produce Paper fixtures.

    Each matching file becomes one Paper whose:
    - ``doi`` = ``github:{org}/{repo}/{path}@{sha[:8]}`` (unique synthetic ID)
    - ``full_text`` = processed file content
    - ``content_type`` = "docs" for markdown, "code" for py/ipynb
    - ``metadata.mined_dois`` = DOIs extracted from the file text
    """
    files = walk_filtered(
        root,
        include=manifest.content.include,
        exclude=manifest.content.exclude,
    )
    papers: list[Paper] = []
    sha_short = commit_sha[:8] if commit_sha else "unknown"

    for rel_path in files:
        abs_path = root / rel_path
        try:
            raw = abs_path.read_bytes()
        except OSError:
            continue
        if len(raw) > max_file_bytes:
            logger.info("github_file_too_large_skipped", path=str(rel_path), size=len(raw))
            continue

        suffix = rel_path.suffix.lower()
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            continue

        if suffix in (".py",):
            full_text = _extract_py_docstrings(text)
            content_type = "code"
        elif suffix == ".ipynb":
            full_text = _strip_notebook(text)
            content_type = "code"
        else:
            full_text = text
            content_type = "docs"

        # Extract inline DOI references
        link_bag = extract_links_from_text(text)
        paper_id = f"github:{manifest.name}/{rel_path.as_posix()}@{sha_short}"

        paper = Paper(
            id=paper_id,
            doi=paper_id,
            title=f"{manifest.name}: {rel_path.as_posix()}",
            year=None,
            source=PaperSource.LOCAL,
            content_type=content_type,
            full_text=full_text,
            metadata={
                "bundle_name": manifest.name,
                "commit_sha": commit_sha,
                "file_path": rel_path.as_posix(),
                "mined_dois": link_bag.dois,
            },
        )
        papers.append(paper)

    return papers
