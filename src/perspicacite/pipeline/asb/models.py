"""Pydantic models for parsed ASB artifacts.

These are *parsed* records — pure data, no behavior. The chunk
producer converts them into Paper objects with metadata.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParsedTool(BaseModel):
    """One tool record (from tools.json or tools/{slug}.json registry)."""
    model_config = ConfigDict(extra="allow")  # ASB schema may evolve

    slug: str | None = None
    name: str
    canonical_url: str | None = None
    install: str | None = None
    role: str | None = None  # frontmatter-only field
    related_skills: list[str] = Field(default_factory=list)
    source_task_ids: list[str] = Field(default_factory=list)
    source_paper_doi: str | None = None
    source_paper_title: str | None = None
    evidence_spans: list[str] = Field(default_factory=list)


class ParsedEnvironment(BaseModel):
    model_config = ConfigDict(extra="allow")
    language: str | None = None
    version: str | None = None
    packages: list[str] = Field(default_factory=list)
    dockerfile_hint: str | None = None


class ParsedParameter(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str | None = None
    typical: str | None = None
    min: Any | None = None
    max: Any | None = None
    units: str | None = None
    source_citation: str | None = None
    source_doi: str | None = None


class ParsedPaperRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    role: str | None = None


class ParsedLink(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    category: str
    source: str | None = None
    surrounding_text: str | None = None


class ParsedSkill(BaseModel):
    """Result of parsing one skills/{slug}/ directory."""
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    description: str
    edam_operation: str | None = None
    edam_topics: list[str] = Field(default_factory=list)
    when_to_use_negative: list[str] = Field(default_factory=list)
    schema_version: str | None = None

    body_markdown: str = ""           # skill.md body (post-frontmatter)
    tools: list[ParsedTool] = Field(default_factory=list)
    environments: list[ParsedEnvironment] = Field(default_factory=list)
    parameters: list[ParsedParameter] = Field(default_factory=list)
    papers: list[ParsedPaperRef] = Field(default_factory=list)
    links: list[ParsedLink] = Field(default_factory=list)
    asb_task_ids: list[str] = Field(default_factory=list)

    bundle_dir: str = ""              # relative path under the run dir
