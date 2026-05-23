"""Top-level ASB-run ingestion.

Steps:
  1. Parse skills (skill_parser.parse_skill_bundle)
  2. Parse workflow cards (card_parser.parse_cards)
  3. Load workflow DAG (dag.load_workflow_dag)
  4. Get/create the KB (composite, or per-skill when mode='per-skill')
  5. For each ParsedSkill: build a Paper + add to KB; ingest backing-paper
     DOIs via existing ingest_dois_into_kb
  6. For each ParsedCard: build a Paper (with DAG neighbors) + add
  7. Store workflow_dag.json contents on the KB description
  8. Write skill_kb.json entries per skill

The two seam helpers — `_make_or_get_kb` and `_ingest_backing_paper_dois`
— are module-level so tests can patch them. In production, the orchestrator
is invoked from the MCP tool / CLI handler with a populated `app_state`;
both helpers consume `app_state` from there.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from perspicacite.pipeline.asb.card_parser import parse_cards
from perspicacite.pipeline.asb.chunk_producer import card_to_paper, skill_to_paper
from perspicacite.pipeline.asb.dag import WorkflowDag, load_workflow_dag
from perspicacite.pipeline.asb.models import ParsedCard, ParsedSkill
from perspicacite.pipeline.asb.skill_kb_writer import write_skill_kb_entries
from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle

logger = logging.getLogger(__name__)


async def ingest_asb_run(
    *,
    asb_run_dir: str | Path,
    kb_name: str | None = None,
    include: Iterable[str] = ("skills", "workflows"),
    mode: str = "composite",
    update_skill_kb_json: bool = True,
    app_state: Any = None,
) -> dict[str, Any]:
    """Ingest an ASB run directory into one or more Perspicacité KBs.

    Args:
        asb_run_dir: Path to the run dir (must contain skills/_index.json
            and/or cards/).
        kb_name: KB to write to. Defaults to the run-dir name.
        include: Subset of {"skills", "workflows"}.
        mode: "composite" (single KB) or "per-skill" (one KB per skill;
            workflows still go to the composite KB).
        update_skill_kb_json: Write back the integration seam.
        app_state: Application state object (for KB creation + DOI ingest).
            May be None in tests when both seam helpers are patched.

    Returns:
        Dict with:
          - kb_names: list of KBs created/updated
          - skills_ingested: int
          - workflows_ingested: int
          - papers_ingested: int (total Paper objects added)
          - failed: list of (paper_id, error)
          - workflow_dag: nodes/edges as dict (or None when DAG empty)
    """
    include_set = set(include)
    if not include_set:
        raise ValueError("include must contain at least one of {skills, workflows}")
    if mode not in ("composite", "per-skill"):
        raise ValueError("mode must be 'composite' or 'per-skill'")

    run_dir = Path(asb_run_dir)
    skills = parse_skill_bundle(run_dir) if "skills" in include_set else []
    cards = parse_cards(run_dir) if "workflows" in include_set else []
    dag = load_workflow_dag(run_dir)

    composite_name = kb_name or run_dir.name
    kb_names: list[str] = []
    papers_ingested = 0
    failed: list[dict] = []

    composite_kb = await _make_or_get_kb(
        composite_name,
        description=_kb_description(skills=skills, cards=cards, dag=dag),
        app_state=app_state,
    )
    kb_names.append(composite_name)

    # ---- Skills ---------------------------------------------------------
    for skill in skills:
        target_kb = composite_kb
        target_kb_name = composite_name
        if mode == "per-skill":
            per_skill_name = f"{composite_name}__{skill.slug}"
            target_kb = await _make_or_get_kb(
                per_skill_name,
                description=_kb_description(skills=[skill], cards=[], dag=dag),
                app_state=app_state,
            )
            target_kb_name = per_skill_name
            kb_names.append(per_skill_name)

        # 1. Skill body
        skill_entries: list[dict] = []
        try:
            paper = skill_to_paper(skill)
            await target_kb.add_papers([paper])
            papers_ingested += 1
            skill_entries.append({
                "kind": "skill_body",
                "source_url": f"skills/{skill.slug}/skill.md",
                "kb_name": target_kb_name,
                "chunk_ids": [],
                "chunk_count": 0,
                "bytes": len((skill.body_markdown or "").encode("utf-8")),
                "content_type": "text",
                "embedding_model": "text-embedding-3-small",
                "ingested_at": _now_iso(),
            })
        except Exception as e:
            logger.warning("asb_skill_add_failed", extra={
                "slug": skill.slug, "error": str(e)
            })
            failed.append({"id": f"asb_skill:{skill.slug}", "error": str(e)})

        # 2. Backing-paper DOIs (existing literature ingest path)
        skill_dois = [p.doi for p in skill.papers if p.doi]
        if skill_dois:
            try:
                await _ingest_backing_paper_dois(
                    kb=target_kb, dois=skill_dois, app_state=app_state,
                )
            except Exception as e:
                logger.warning("asb_skill_backing_dois_failed", extra={
                    "slug": skill.slug, "error": str(e)
                })

        # 3. skill_kb.json round-trip
        if update_skill_kb_json and skill_entries:
            skill_kb_path = run_dir / "skills" / skill.slug / "skill_kb.json"
            if skill_kb_path.exists():
                try:
                    write_skill_kb_entries(skill_kb_path, entries=skill_entries)
                except Exception as e:
                    logger.warning("asb_skill_kb_write_failed", extra={
                        "slug": skill.slug, "error": str(e)
                    })

    # ---- Workflow cards (always composite KB) ---------------------------
    for card in cards:
        try:
            paper = card_to_paper(card, dag=dag)
            await composite_kb.add_papers([paper])
            papers_ingested += 1
        except Exception as e:
            logger.warning("asb_card_add_failed", extra={
                "task_id": card.task_id, "error": str(e)
            })
            failed.append({"id": f"asb_card:{card.task_id}", "error": str(e)})

    return {
        "kb_names": kb_names,
        "skills_ingested": len(skills),
        "workflows_ingested": len(cards),
        "papers_ingested": papers_ingested,
        "failed": failed,
        "workflow_dag": dag.to_dict() if dag.nodes else None,
    }


def _kb_description(
    *,
    skills: list[ParsedSkill],
    cards: list[ParsedCard],
    dag: WorkflowDag,
) -> str:
    """Serialise bundle-level metadata onto the KB description.

    Stored as a JSON blob inside a descriptive prefix so the
    auto-KB-routing layer can extract it.
    """
    payload = {
        "skills": [s.slug for s in skills],
        "task_ids": [c.task_id for c in cards],
        "workflow_dag": dag.to_dict() if dag.nodes else None,
    }
    return (
        "ASB bundle ingest. Includes skills + workflow cards + "
        f"DAG metadata. {json.dumps(payload)}"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _make_or_get_kb(name: str, *, description: str = "", app_state: Any = None):
    """Get or create a KB by name. Production: routes through app_state's
    session_store + DynamicKnowledgeBase factory. Patched in tests.

    NOTE for D8/D9 wiring: the production helper in search_to_kb.py is
    named ``_create_kb_if_missing(app_state, kb_name, description)`` and
    returns ``(kb_meta, created)`` — it does NOT return a DynamicKnowledgeBase
    directly. The production path below sketches the two-step dance needed:
    first call ``_create_kb_if_missing``, then construct a DynamicKnowledgeBase
    from the returned metadata + app_state stores.
    """
    if app_state is None:
        raise RuntimeError(
            "_make_or_get_kb requires app_state in production. "
            "Tests should patch this function."
        )
    # Production path: re-use search_to_kb's _create_kb_if_missing pattern.
    from perspicacite.pipeline.search_to_kb import _create_kb_if_missing
    kb_meta, _created = await _create_kb_if_missing(
        app_state, name, description,
    )
    # Construct the in-memory DynamicKnowledgeBase backed by that metadata.
    from perspicacite.models.kb import chroma_collection_name_for_kb
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig

    config = KnowledgeBaseConfig(
        collection_prefix=chroma_collection_name_for_kb(name) + "_",
    )
    kb = DynamicKnowledgeBase(
        vector_store=app_state.vector_store,
        embedding_service=app_state.embedding_service,
        config=config,
    )
    kb.name = name
    kb.description = description
    return kb


async def _ingest_backing_paper_dois(
    *, kb: Any, dois: list[str], app_state: Any,
) -> dict[str, Any]:
    """Run the existing DOI ingest path. Patched in tests."""
    if app_state is None:
        raise RuntimeError(
            "_ingest_backing_paper_dois requires app_state in production. "
            "Tests should patch this function."
        )
    from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb
    return await ingest_dois_into_kb(
        app_state, kb_name=getattr(kb, "name", "asb_bundle"), dois=dois,
    )
