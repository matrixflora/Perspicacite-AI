"""Parser for ASB-Skill collection v1 release format.

Reads a directory laid out as:
  collection.yaml          <- LinkML SkillCollection instance
  tools.lock.yaml          <- frozen tool IRI@hash refs
  tools/<slug>.yaml        <- per-tool records
  catalogue.jsonld         <- skill index (JSON-LD)
  skills/<slug>/SKILL.md   <- per-skill markdown (frontmatter + body)

Returns a ParsedSkillCollection with one ParsedCollectionSkill per skill.
These records are consumed by collection_ingest.py; the collection_parser
itself has no I/O side effects (no KB writes, no network calls).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ParsedCollectionSkill:
    """One skill from an ASB-Skill collection release."""
    slug: str
    name: str
    description: str
    iri: str = ""
    collection_iri: str = ""
    edam_operation: str = ""
    edam_topics: list[str] = field(default_factory=list)
    derived_from_dois: list[str] = field(default_factory=list)
    tool_iris: list[str] = field(default_factory=list)

    # Body sections (split from SKILL.md body)
    overview_chunk: str = ""
    procedure_chunk: str = ""
    tools_chunk: str = ""   # generated from tools.lock.yaml + tools/<slug>.yaml


@dataclass
class ParsedSkillCollection:
    """Result of parsing one collection directory."""
    name: str
    collection_iri: str = ""
    edam_topics: list[str] = field(default_factory=list)
    skills: list[ParsedCollectionSkill] = field(default_factory=list)
    catalogue_entries: list[dict[str, Any]] = field(default_factory=list)


def parse_skill_collection(collection_dir: Path | str) -> ParsedSkillCollection:
    """Parse an ASB-Skill collection v1 directory.

    Args:
        collection_dir: Path to the root of the collection (containing
            collection.yaml, tools.lock.yaml, skills/, etc.)

    Returns:
        ParsedSkillCollection with one ParsedCollectionSkill per skill entry.

    Raises:
        FileNotFoundError: If collection.yaml is not found.
    """
    collection_dir = Path(collection_dir)
    coll_yaml_path = collection_dir / "collection.yaml"
    if not coll_yaml_path.exists():
        raise FileNotFoundError(
            f"ASB-Skill collection.yaml not found at {coll_yaml_path}"
        )

    coll_meta = _load_yaml(coll_yaml_path) or {}
    name = coll_meta.get("name") or collection_dir.name
    collection_iri = coll_meta.get("id") or ""
    edam_topics: list[str] = coll_meta.get("edam_topics") or []

    # Load tools registry (tools.lock.yaml + tools/*.yaml)
    tool_registry = _build_tool_registry(collection_dir)

    # Load catalogue.jsonld
    catalogue_entries = _load_catalogue(collection_dir)

    # Walk each skill listed in collection.yaml
    skill_entries = coll_meta.get("skills") or []
    skills: list[ParsedCollectionSkill] = []
    for entry in skill_entries:
        slug = entry.get("slug") or entry.get("id", "").rstrip("/").split("/")[-1]
        if not slug:
            logger.warning("collection_skill_entry_missing_slug: %s", entry)
            continue
        skill_dir = collection_dir / "skills" / slug
        if not skill_dir.is_dir():
            logger.warning("collection_skill_dir_missing: %s", slug)
            continue
        parsed = _parse_collection_skill(
            skill_dir=skill_dir,
            slug=slug,
            tool_registry=tool_registry,
        )
        skills.append(parsed)

    return ParsedSkillCollection(
        name=name,
        collection_iri=collection_iri,
        edam_topics=edam_topics,
        skills=skills,
        catalogue_entries=catalogue_entries,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_collection_skill(
    *,
    skill_dir: Path,
    slug: str,
    tool_registry: dict[str, dict],
) -> ParsedCollectionSkill:
    """Parse one skills/<slug>/SKILL.md into a ParsedCollectionSkill."""
    skill_md_path = skill_dir / "SKILL.md"
    frontmatter, body = _split_frontmatter(skill_md_path)

    meta = frontmatter.get("metadata") or {}
    description_raw = frontmatter.get("description") or ""
    # Normalize multiline YAML description
    description = " ".join(description_raw.strip().splitlines()).strip()

    edam_topics: list[str] = meta.get("edam_topics") or []
    derived_from_dois: list[str] = [
        ref["doi"] for ref in (meta.get("derived_from") or []) if ref.get("doi")
    ]
    tool_iris: list[str] = meta.get("tools") or []

    # Split body into named sections
    overview_chunk = _extract_section(body, "Overview")
    procedure_chunk = _extract_section(body, "Procedure")

    # Generate tools_chunk from tool_iris resolved against tool_registry
    tools_chunk = _build_tools_chunk(tool_iris, tool_registry)

    return ParsedCollectionSkill(
        slug=slug,
        name=frontmatter.get("name") or slug,
        description=description,
        iri=meta.get("iri") or "",
        collection_iri=meta.get("collection") or "",
        edam_operation=meta.get("edam_operation") or "",
        edam_topics=edam_topics,
        derived_from_dois=derived_from_dois,
        tool_iris=tool_iris,
        overview_chunk=overview_chunk,
        procedure_chunk=procedure_chunk,
        tools_chunk=tools_chunk,
    )


def _split_frontmatter(skill_md: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_str) from a SKILL.md file."""
    if not skill_md.exists():
        return {}, ""
    text = skill_md.read_text()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        logger.warning("collection_skill_frontmatter_unparseable: %s", str(skill_md))
        meta = {}
    body = parts[2].lstrip("\n")
    return meta if isinstance(meta, dict) else {}, body


def _extract_section(body: str, section_name: str) -> str:
    """Extract the text content of a Markdown ## Section by name.

    Returns the body text between the matching heading and the next
    heading of equal or higher level, stripped. Empty string if not found.
    """
    pattern = re.compile(
        r"^##\s+" + re.escape(section_name) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(body)
    if not m:
        return ""
    start = m.end()
    # Find next ## or # heading
    next_heading = re.search(r"^#{1,2}\s", body[start:], re.MULTILINE)
    if next_heading:
        end = start + next_heading.start()
        section_text = body[start:end]
    else:
        section_text = body[start:]
    return section_text.strip()


def _build_tool_registry(collection_dir: Path) -> dict[str, dict]:
    """Build {slug: tool_record} from tools.lock.yaml + tools/*.yaml."""
    registry: dict[str, dict] = {}

    # Load individual tool files first (authoritative content)
    tools_dir = collection_dir / "tools"
    if tools_dir.is_dir():
        for tool_file in sorted(tools_dir.glob("*.yaml")):
            tool_data = _load_yaml(tool_file) or {}
            slug = tool_data.get("slug") or tool_file.stem
            registry[slug] = tool_data

    # Overlay lock file (adds IRI/hash info)
    lock_path = collection_dir / "tools.lock.yaml"
    if lock_path.exists():
        lock_data = _load_yaml(lock_path) or {}
        for tool_ref in lock_data.get("tools") or []:
            slug = tool_ref.get("slug") or ""
            if slug and slug in registry:
                registry[slug]["iri"] = tool_ref.get("iri") or registry[slug].get("iri")
            elif slug:
                registry[slug] = tool_ref

    return registry


def _build_tools_chunk(tool_iris: list[str], tool_registry: dict[str, dict]) -> str:
    """Generate a prose tools chunk from IRI list + tool registry."""
    if not tool_iris and not tool_registry:
        return ""
    lines = []
    # Resolve each tool IRI against the registry (match by IRI prefix or slug)
    matched: list[dict] = []
    for iri in tool_iris:
        # Try slug-based match: last path segment before any @sha
        slug_part = iri.rstrip("/").split("/")[-1].split("@")[0]
        if slug_part in tool_registry:
            matched.append(tool_registry[slug_part])
    # Fall back: include all registry tools when IRI list is empty
    if not matched and not tool_iris:
        matched = list(tool_registry.values())

    for tool in matched:
        name = tool.get("name") or tool.get("slug") or "unknown"
        url = tool.get("canonical_url") or ""
        role = tool.get("role") or ""
        edam_op = tool.get("edam_operation") or ""
        parts = [f"- **{name}**"]
        if role:
            parts.append(f"  Role: {role}")
        if url:
            parts.append(f"  URL: {url}")
        if edam_op:
            parts.append(f"  EDAM operation: {edam_op}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _load_catalogue(collection_dir: Path) -> list[dict]:
    """Parse catalogue.jsonld into a flat list of hasPart entries."""
    cat_path = collection_dir / "catalogue.jsonld"
    if not cat_path.exists():
        return []
    try:
        data = json.loads(cat_path.read_text())
    except json.JSONDecodeError:
        logger.warning("catalogue_jsonld_unparseable: %s", str(cat_path))
        return []
    parts = data.get("hasPart") or []
    entries = []
    for part in parts:
        entries.append({
            "id": part.get("identifier") or part.get("@id") or "",
            "name": part.get("name") or "",
            "description": part.get("description") or "",
        })
    return entries


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError) as e:
        logger.warning("yaml_load_failed: %s — %s", str(path), e)
        return None
