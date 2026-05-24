"""Orchestrator for ingesting an ASB-Skill collection v1 directory into Perspicacité.

Reads the release layout via collection_parser.ParsedSkillCollection, converts
each skill into chunk-ready Paper objects via _skill_to_papers(), writes
kb_metadata/ side-files (ontology_refs.json, skill_index.json), and optionally
ingests derived_from DOIs through the existing DOI pipeline.

Seam helpers (_make_or_get_kb, _ingest_backing_paper_dois) are imported from
run_ingest.py so they are module-level patchable in tests — the same pattern
used by run_ingest itself.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.asb.collection_parser import (
    ParsedCollectionSkill,
    ParsedSkillCollection,
    parse_skill_collection,
)

# Re-use the seam helpers from run_ingest so tests can patch them uniformly.
from perspicacite.pipeline.asb.run_ingest import (
    _ingest_backing_paper_dois,
    _make_or_get_kb,
)

logger = logging.getLogger(__name__)


async def ingest_asb_skill_collection(
    *,
    collection_dir: Path | str,
    kb_name: str,
    app_state: Any,
    ingest_linked_papers: bool = True,
) -> dict[str, Any]:
    """Ingest an ASB-Skill collection v1 directory into a Perspicacité KB.

    Steps:
      1. Parse the collection via collection_parser.
      2. For each skill: produce up to three Paper objects (summary, procedure,
         tools) and add them to the target KB.
      3. Write kb_metadata/ontology_refs.json and kb_metadata/skill_index.json.
      4. Optionally ingest derived_from DOIs via the existing DOI pipeline.

    Args:
        collection_dir: Root of the collection (contains collection.yaml).
        kb_name: Target KB name; created if it doesn't exist.
        app_state: Application state object (for KB creation + DOI ingest).
        ingest_linked_papers: If True, DOIs from derived_from fields are
            ingested via ingest_dois_into_kb.

    Returns:
        Dict with kb_name, collection_name, skills_ingested, papers_added,
        failed (list), kb_metadata_written (list of paths).
    """
    collection_dir = Path(collection_dir)
    collection = parse_skill_collection(collection_dir)

    kb = await _make_or_get_kb(
        kb_name,
        description=_kb_description(collection),
        app_state=app_state,
    )

    papers_added = 0
    failed: list[dict] = []

    for skill in collection.skills:
        try:
            papers = _skill_to_papers(skill)
            await kb.add_papers(papers)
            papers_added += len(papers)
        except Exception as e:
            logger.warning(
                "collection_skill_add_failed",
                extra={"slug": skill.slug, "error": str(e)},
            )
            failed.append({"id": f"asb_collection_skill:{skill.slug}", "error": str(e)})

        # Ingest backing DOIs
        if ingest_linked_papers and skill.derived_from_dois:
            try:
                await _ingest_backing_paper_dois(
                    kb=kb, dois=skill.derived_from_dois, app_state=app_state,
                )
            except Exception as e:
                logger.warning(
                    "collection_skill_doi_ingest_failed",
                    extra={"slug": skill.slug, "error": str(e)},
                )

    # Write kb_metadata/ side-files
    kb_metadata_written = _write_kb_metadata(collection_dir, collection)

    return {
        "kb_name": kb_name,
        "collection_name": collection.name,
        "skills_ingested": len(collection.skills),
        "papers_added": papers_added,
        "failed": failed,
        "kb_metadata_written": kb_metadata_written,
    }


def _skill_to_papers(skill: ParsedCollectionSkill) -> list[Paper]:
    """Produce up to three Paper objects per skill (summary, procedure, tools)."""
    papers: list[Paper] = []
    base_meta = {
        "content_kind": "asb_collection_skill",
        "skill_slug": skill.slug,
        "skill_name": skill.name,
        "skill_iri": skill.iri,
        "collection_iri": skill.collection_iri,
        "edam_operation": skill.edam_operation,
        "edam_topics": list(skill.edam_topics),
        "derived_from_dois": list(skill.derived_from_dois),
        "tool_iris": list(skill.tool_iris),
    }

    if skill.overview_chunk:
        papers.append(Paper(
            id=f"asb_collection:{skill.slug}:summary",
            title=f"{skill.name} — Overview",
            abstract=skill.description,
            full_text=skill.overview_chunk,
            source=PaperSource.SKILL_BUNDLE,
            metadata={**base_meta, "chunk_type": "summary"},
        ))

    if skill.procedure_chunk:
        papers.append(Paper(
            id=f"asb_collection:{skill.slug}:procedure",
            title=f"{skill.name} — Procedure",
            abstract=skill.description,
            full_text=skill.procedure_chunk,
            source=PaperSource.SKILL_BUNDLE,
            metadata={**base_meta, "chunk_type": "procedure"},
        ))

    if skill.tools_chunk:
        papers.append(Paper(
            id=f"asb_collection:{skill.slug}:tools",
            title=f"{skill.name} — Tools",
            abstract=skill.description,
            full_text=skill.tools_chunk,
            source=PaperSource.SKILL_BUNDLE,
            metadata={**base_meta, "chunk_type": "tools"},
        ))

    # Fallback: if no sections were found, use the full description
    if not papers:
        papers.append(Paper(
            id=f"asb_collection:{skill.slug}",
            title=skill.name,
            abstract=skill.description,
            full_text=skill.description,
            source=PaperSource.SKILL_BUNDLE,
            metadata={**base_meta, "chunk_type": "fallback"},
        ))

    return papers


def _write_kb_metadata(
    collection_dir: Path, collection: ParsedSkillCollection
) -> list[str]:
    """Write kb_metadata/ side-files. Returns list of written file paths."""
    kb_meta_dir = collection_dir / "kb_metadata"
    kb_meta_dir.mkdir(exist_ok=True)
    written: list[str] = []

    # ontology_refs.json — collection-level EDAM IRIs
    ontology_refs_path = kb_meta_dir / "ontology_refs.json"
    ontology_refs_path.write_text(json.dumps(
        {
            "collection_iri": collection.collection_iri,
            "edam_topics": collection.edam_topics,
            "skills": [
                {
                    "slug": s.slug,
                    "edam_operation": s.edam_operation,
                    "edam_topics": s.edam_topics,
                }
                for s in collection.skills
            ],
            "generated_at": _now_iso(),
        },
        indent=2,
    ))
    written.append(str(ontology_refs_path))

    # skill_index.json — catalogue.jsonld entries
    skill_index_path = kb_meta_dir / "skill_index.json"
    skill_index_path.write_text(json.dumps(
        {
            "collection": collection.name,
            "skills": collection.catalogue_entries,
            "generated_at": _now_iso(),
        },
        indent=2,
    ))
    written.append(str(skill_index_path))

    return written


def _kb_description(collection: ParsedSkillCollection) -> str:
    payload = json.dumps({
        "collection_iri": collection.collection_iri,
        "edam_topics": collection.edam_topics,
    })
    return (
        f"ASB-Skill collection v1: {collection.name}. "
        f"{len(collection.skills)} skills. "
        + payload
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
