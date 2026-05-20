"""Frontmatter sanity test for the perspicacite-mcp skill.

Ensures the skill file exists and carries the minimal YAML frontmatter
(``name:`` and ``description:``) that the skill loader expects.
"""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = Path(".claude/skills/perspicacite-mcp/SKILL.md")


def test_skill_frontmatter() -> None:
    assert SKILL_PATH.exists(), f"missing skill file: {SKILL_PATH}"

    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must start with a YAML frontmatter delimiter"

    # Extract the frontmatter block between the first two '---' fences.
    parts = text.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must contain a closed --- ... --- frontmatter block"
    frontmatter = parts[1]

    assert "name:" in frontmatter, "frontmatter must contain a name: field"
    assert "description:" in frontmatter, "frontmatter must contain a description: field"
