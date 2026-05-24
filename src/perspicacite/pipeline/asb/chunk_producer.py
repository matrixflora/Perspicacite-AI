"""Convert parsed ASB records → Paper objects.

Each ASB skill / workflow card maps to one Paper with
``source=PaperSource.SKILL_BUNDLE`` and the structured fields in
``Paper.metadata``. The existing chunker reads Paper.full_text and
propagates Paper.metadata onto chunk metadata.

Paper IDs are stable (asb_skill:{slug} / asb_card:{task_id}) so
re-ingest is idempotent against ``DynamicKnowledgeBase._paper_ids``.

Metadata fields cover both 2026-05-15 and 2026-05-16 ASB schemas;
2026-05-16-only fields are passed through as-is (None or default
when the source didn't have them).
"""
from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.asb.dag import WorkflowDag
from perspicacite.pipeline.asb.models import ParsedCard, ParsedSkill


def skill_to_paper(skill: ParsedSkill) -> Paper:
    """Return a Paper carrying the skill body + structured metadata."""
    md = {
        "content_kind": "skill_body",
        "skill_id": skill.slug,
        "skill_name": skill.name,
        "skill_description": skill.description,
        "edam_operation": skill.edam_operation,
        "edam_topics": list(skill.edam_topics),
        "tools": [t.model_dump() for t in skill.tools],
        "environment": [e.model_dump() for e in skill.environments],
        "parameters": [p.model_dump() for p in skill.parameters],
        "when_to_use_negative": list(skill.when_to_use_negative),
        "asb_task_ids": list(skill.asb_task_ids),
        "schema_version": skill.schema_version,
    }
    return Paper(
        id=f"asb_skill:{skill.slug}",
        title=skill.name,
        abstract=skill.description,
        full_text=skill.body_markdown,
        source=PaperSource.SKILL_BUNDLE,
        metadata=md,
    )


def card_to_paper(card: ParsedCard, *, dag: WorkflowDag | None) -> Paper:
    """Return a Paper carrying the card body + structured metadata.

    The 2026-05-16 schema fields (task_objective, executable, task_inputs/
    outputs, execution_profile, ...) ride along in the metadata dict and
    surface on chunks via the existing Paper.metadata → ChunkMetadata
    propagation.
    """
    md = {
        "content_kind": "workflow_card",
        "task_id": card.task_id,
        "task_card_title": card.title,
        # 2026-05-16 task_objective; absent on 2026-05-15 cards
        "task_objective": card.task_objective,
        "article_type": card.article_type,
        "domain": card.domain,
        "primary_domain": card.primary_domain,
        "subdomains": list(card.subdomains),
        "techniques": list(card.techniques),
        "subtask_categories": list(card.subtask_categories),
        "tools_used": list(card.tools_used),
        "skills_used": list(card.skills_used),
        "paper_doi": card.crossref_doi,
        # Prefer the 2026-05-16 'github_name', fall back to legacy 'github'
        "paper_github": card.github_name or card.github,
        "inputs": list(card.data_in),
        "task_inputs": list(card.task_inputs),
        "task_outputs": list(card.task_outputs),
        "expected_outputs": list(card.expected_outputs),
        "expected_artifact_name": card.expected_artifact_name,
        "parameters": list(card.parameters),
        "evaluation_strategy": dict(card.evaluation_strategy),
        # 2026-05-16 execution fields
        "executable": card.executable,                    # dict or None
        "execution_profile": dict(card.execution_profile),
        "execution_environment": card.execution_environment,
        "run_command": card.run_command,
        "run_cwd": card.run_cwd,
        "run_timeout_seconds": card.run_timeout_seconds,
        "reproducibility_tier": card.reproducibility_tier,
        "linked_result_ids": list(card.linked_result_ids),
        "provenance_source": card.provenance_source,
        "source_package": card.source_package,
        "scenario_id": card.scenario_id,
        "schema_version": card.schema_version,
        "upstream_tasks": dag.upstream(card.task_id) if dag else [],
        "downstream_tasks": dag.downstream(card.task_id) if dag else [],
    }
    return Paper(
        id=f"asb_card:{card.task_id}",
        title=card.title,
        abstract=card.task_objective or "",
        full_text=card.body_markdown,
        source=PaperSource.SKILL_BUNDLE,
        doi=card.crossref_doi,
        metadata=md,
    )
