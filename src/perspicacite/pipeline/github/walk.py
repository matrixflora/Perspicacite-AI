"""File walker that respects include/exclude glob patterns."""
from __future__ import annotations

from pathlib import Path  # noqa: TC003


def walk_filtered(
    root: Path,
    *,
    include: list[str],
    exclude: list[str],
) -> list[Path]:
    """Return relative paths under ``root`` matching any include glob
    and not matching any exclude glob.

    Uses ``pathspec`` when available; falls back to fnmatch-based globbing.
    """
    all_files: list[Path] = []
    try:
        import pathspec  # type: ignore[import-untyped]
        include_spec = pathspec.PathSpec.from_lines("gitwildmatch", include)
        exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            rel_str = rel.as_posix()
            if include_spec.match_file(rel_str) and not exclude_spec.match_file(rel_str):
                all_files.append(rel)
    except ImportError:
        import fnmatch

        def _matches_any(rel_str: str, patterns: list[str]) -> bool:
            """Match rel_str against gitignore-style patterns using fnmatch.

            For ``**/*.ext`` patterns we also test the bare filename so that
            top-level files like ``foo.py`` match ``**/*.py``.
            """
            name = rel_str.rsplit("/", 1)[-1]  # basename
            for pat in patterns:
                if fnmatch.fnmatch(rel_str, pat):
                    return True
                # **/*.ext should match both subdir/file.ext AND top-level file.ext
                if pat.startswith("**/"):
                    bare = pat[3:]  # strip leading **/
                    if fnmatch.fnmatch(name, bare) or fnmatch.fnmatch(rel_str, bare):
                        return True
            return False

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            rel_str = rel.as_posix()
            if _matches_any(rel_str, include) and not _matches_any(rel_str, exclude):
                all_files.append(rel)
    return sorted(all_files)
