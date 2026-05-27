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

Access-tier redaction (audit item N7, 2026-05-27):
``skill_to_paper`` and ``card_to_paper`` accept an optional
``non_oa_dois`` set. When the record's source DOI is in that set, OR
when the DOI is absent from the build's corpus.yaml entirely (fail-safe:
unknown access is treated as non-OA), the Evidence section is stripped
from ``Paper.full_text`` and replaced with a redaction marker. All
structured metadata (DOI, title, authors, tool/parameter records) is
preserved — only the verbatim Evidence quote section is excised.
"""
from __future__ import annotations

import re

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.asb.dag import WorkflowDag
from perspicacite.pipeline.asb.models import ParsedCard, ParsedSkill

# Match a markdown "## Evidence" heading and everything up to the next
# top-level "## " heading or end-of-document. Used to excise verbatim
# Evidence quotes from skill.md / card.md bodies derived from non-OA
# (or unknown-access) source papers.
_EVIDENCE_SECTION_RE = re.compile(
    r"## Evidence.*?(?=\n## |\Z)",
    flags=re.DOTALL,
)
_EVIDENCE_REDACTED_REPLACEMENT = (
    "## Evidence\n[REDACTED — non-OA source]\n\n"
)


def _redact_evidence_section(body_markdown: str) -> str:
    """Replace the ``## Evidence`` section with a redaction marker.

    Preserves all other markdown sections (Overview, Procedure, etc.).
    No-op when the body has no Evidence section.
    """
    if not body_markdown:
        return body_markdown
    return _EVIDENCE_SECTION_RE.sub(
        _EVIDENCE_REDACTED_REPLACEMENT,
        body_markdown,
    )


def _should_redact(source_dois: list[str], non_oa_dois: set[str] | None) -> bool:
    """Decide whether to redact the Evidence section.

    Fail-safe semantics: when ``non_oa_dois`` is provided (i.e., a
    corpus.yaml was found at ingest time) and the record's source DOIs
    are EITHER explicitly in the non-OA set OR absent from the corpus
    entirely (unknown), redact. When ``non_oa_dois`` is None, the
    caller has opted out of the gate (allow_non_oa_ingest=True OR no
    corpus.yaml present and operator chose to proceed) — no redaction.
    """
    if non_oa_dois is None:
        return False
    # No source DOI on the record → can't prove OA → fail safe and redact.
    sds = [d for d in source_dois if d]
    if not sds:
        return True
    sds_lower = {d.lower().strip() for d in sds}
    # If any source DOI is non-OA OR unknown (not in the corpus.yaml at
    # all), redact. We can't compare against the full corpus DOI set
    # here without threading it in, so the caller passes a non_oa_dois
    # set that already includes "unknown" entries (caller responsibility).
    return bool(sds_lower & non_oa_dois)


def skill_to_paper(
    skill: ParsedSkill,
    *,
    non_oa_dois: set[str] | None = None,
) -> Paper:
    """Return a Paper carrying the skill body + structured metadata.

    Args:
        skill: Parsed skill record.
        non_oa_dois: Optional set of lower-cased DOIs that are non-OA
            OR unknown-access in the run-dir's corpus.yaml. When the
            skill's source DOIs intersect this set (or the skill has no
            source DOI at all and the gate is active), the Evidence
            section in ``Paper.full_text`` is replaced with a redaction
            marker. Pass ``None`` to bypass the gate entirely (operator
            opt-in via ``allow_non_oa_ingest=True`` upstream).
    """
    source_dois = [p.doi for p in skill.papers if p.doi]
    full_text = skill.body_markdown
    if _should_redact(source_dois, non_oa_dois):
        full_text = _redact_evidence_section(full_text)

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
        full_text=full_text,
        source=PaperSource.SKILL_BUNDLE,
        metadata=md,
    )


def card_to_paper(
    card: ParsedCard,
    *,
    dag: WorkflowDag | None,
    non_oa_dois: set[str] | None = None,
) -> Paper:
    """Return a Paper carrying the card body + structured metadata.

    The 2026-05-16 schema fields (task_objective, executable, task_inputs/
    outputs, execution_profile, ...) ride along in the metadata dict and
    surface on chunks via the existing Paper.metadata → ChunkMetadata
    propagation.

    See ``skill_to_paper`` for ``non_oa_dois`` semantics.
    """
    source_dois = [card.crossref_doi] if card.crossref_doi else []
    full_text = card.body_markdown
    if _should_redact(source_dois, non_oa_dois):
        full_text = _redact_evidence_section(full_text)
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
        full_text=full_text,
        source=PaperSource.SKILL_BUNDLE,
        doi=card.crossref_doi,
        metadata=md,
    )
