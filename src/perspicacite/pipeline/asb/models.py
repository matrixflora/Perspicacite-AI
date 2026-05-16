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


class ParsedCard(BaseModel):
    """Result of parsing one cards/task_NNN.{md,json} pair.

    Carries both 2026-05-15 and 2026-05-16 schema fields. Older
    cards leave the 2026-05-16-only fields at their defaults.
    """
    model_config = ConfigDict(extra="allow")

    # 2026-05-15 base fields
    task_id: str                              # "task_001"
    title: str = ""                           # human-readable card title
    article_type: str | None = None
    domain: str | None = None
    primary_domain: str | None = None
    subdomains: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    subtask_categories: list[str] = Field(default_factory=list)
    crossref_doi: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    data_in: list[dict] = Field(default_factory=list)
    data_out: list[dict] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    landmark_outputs: list[str] = Field(default_factory=list)
    parameters: list[dict] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)
    evaluation_strategy: dict = Field(default_factory=dict)
    methodology_summary: list[str] = Field(default_factory=list)
    body_markdown: str = ""
    schema_version: str | None = None
    workflow_ports: dict = Field(default_factory=dict)

    # 2026-05-15 vs 2026-05-16 (either name acceptable)
    github: str | None = None         # 2026-05-15 'github' field
    github_name: str | None = None    # 2026-05-16 rename

    # 2026-05-16 NEW fields (all optional, default to empty/None)
    task_objective: str | None = None
    task_inputs: list[dict] = Field(default_factory=list)
    task_outputs: list[dict] = Field(default_factory=list)
    executable: dict | None = None   # 2026-05-16: structured (was bool in 2026-05-15)
    execution_profile: dict = Field(default_factory=dict)
    execution_environment: dict | None = None
    run_command: str | None = None
    run_cwd: str | None = None
    run_timeout_seconds: float | None = None
    reproducibility_tier: str | None = None
    expected_artifact_name: str | None = None
    linked_result_ids: list[str] = Field(default_factory=list)
    provenance_source: str | None = None
    source_package: str | None = None
    scenario_id: str | None = None
    evidence_snippets: list[dict] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
