"""Parser for ASB workflow cards (cards/task_NNN.{md,json}).

A card is a richly-structured scientific task description. The .json
sidecar is the source of truth for structured fields; the .md file
carries the human-readable body that gets chunked + embedded.

Tolerates both 2026-05-15 and 2026-05-16 schemas:

  - executable: bool (old) OR dict with cmd/env (new) — passed through
    only when it's a dict; bool form is dropped (info not useful in v1)
  - github (old) and github_name (new) — populate whichever the source
    provided; chunk producer prefers github_name
  - task_objective, task_inputs/outputs, execution_profile, etc. —
    2026-05-16-only fields; default empty when absent
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from perspicacite.pipeline.asb.models import ParsedCard

logger = logging.getLogger(__name__)


def parse_cards(run_dir: Path | str) -> list[ParsedCard]:
    """Return one ParsedCard per task_NNN.json + matching .md under cards/.

    Pairs missing one half are skipped with a warning. Missing cards/
    directory returns []."""
    run_dir = Path(run_dir)
    cards_dir = run_dir / "cards"
    if not cards_dir.is_dir():
        return []

    json_paths = sorted(cards_dir.glob("task_*.json"))
    out: list[ParsedCard] = []
    for jp in json_paths:
        task_id = jp.stem
        mp = cards_dir / f"{task_id}.md"
        if not mp.exists():
            logger.warning("asb_card_missing_md", extra={"task_id": task_id})
            continue
        try:
            structured = json.loads(jp.read_text())
        except json.JSONDecodeError:
            logger.warning("asb_card_json_unparseable", extra={"path": str(jp)})
            continue
        body = mp.read_text()
        out.append(_card_from_json(task_id=task_id, structured=structured, body=body))
    return out


def _card_from_json(*, task_id: str, structured: dict, body: str) -> ParsedCard:
    """Map the raw .json dict to ParsedCard.

    Field-presence checks for the 2026-05-16 schema — version strings
    are unreliable, but field presence is. ``executable`` is normalised
    to dict-or-None; the bool form (2026-05-15) is dropped because the
    structured dict is what downstream actually needs.
    """
    executable_raw = structured.get("executable")
    executable_dict = executable_raw if isinstance(executable_raw, dict) else None

    return ParsedCard(
        task_id=task_id,
        title=(
            structured.get("title")
            or structured.get("task_objective")
            or structured.get("research_question")
            or task_id
        ),
        article_type=structured.get("article_type"),
        domain=structured.get("domain"),
        primary_domain=structured.get("primary_domain"),
        subdomains=structured.get("subdomains") or [],
        techniques=structured.get("techniques") or [],
        subtask_categories=structured.get("subtask_categories") or [],
        crossref_doi=structured.get("crossref_doi") or structured.get("doi"),
        github=structured.get("github"),
        github_name=structured.get("github_name") or structured.get("github"),
        tools_used=structured.get("tools") or structured.get("tools_used") or [],
        skills_used=structured.get("skills") or structured.get("skills_used") or [],
        data_in=structured.get("data_in") or [],
        data_out=structured.get("data_out") or [],
        expected_outputs=structured.get("expected_outputs") or [],
        landmark_outputs=structured.get("landmark_outputs") or [],
        parameters=structured.get("parameters") or [],
        domain_knowledge=structured.get("domain_knowledge") or [],
        evaluation_strategy=structured.get("evaluation_strategy") or {},
        methodology_summary=structured.get("methodology_summary") or [],
        workflow_ports=structured.get("workflow_ports") or {},
        body_markdown=body,
        schema_version=structured.get("schema_version"),
        # 2026-05-16 fields
        task_objective=structured.get("task_objective"),
        task_inputs=structured.get("task_inputs") or [],
        task_outputs=structured.get("task_outputs") or [],
        executable=executable_dict,
        execution_profile=structured.get("execution_profile") or {},
        execution_environment=structured.get("execution_environment"),
        run_command=structured.get("run_command"),
        run_cwd=structured.get("run_cwd"),
        run_timeout_seconds=structured.get("run_timeout_seconds"),
        reproducibility_tier=structured.get("reproducibility_tier"),
        expected_artifact_name=structured.get("expected_artifact_name"),
        linked_result_ids=structured.get("linked_result_ids") or [],
        provenance_source=structured.get("provenance_source"),
        source_package=structured.get("source_package"),
        scenario_id=structured.get("scenario_id"),
        evidence_snippets=structured.get("evidence_snippets") or [],
        keywords=structured.get("keywords") or [],
    )
