"""Smoke test: the ASB test fixtures are present in the worktree.

This is the lightweight gate for Phase D — if these fixtures aren't
copied, the rest of the ASB ingest tests can't run.
"""
import json
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "asb"


def test_article_878_v4_fixture_has_expected_layout():
    root = FIXTURE_ROOT / "article_878_v4_subset"
    assert root.exists(), f"Primary 2026-05-16 fixture missing: {root}"
    # 2 skills
    skill_dirs = [p for p in (root / "skills").iterdir() if p.is_dir()]
    assert len(skill_dirs) == 2, f"Expected 2 skills, got {len(skill_dirs)}: {skill_dirs}"
    # 3 cards (task_001/002/003 with .json sidecar)
    card_jsons = sorted((root / "cards").glob("task_*.json"))
    assert len(card_jsons) == 3, f"Expected 3 card JSON files, got {card_jsons}"
    # workflow_dag.json present, parses, uses the new dict-edge format
    dag_path = root / "workflow_dag.json"
    dag = json.loads(dag_path.read_text())
    edges = dag.get("edges", [])
    assert edges, "workflow_dag.json has no edges"
    # 2026-05-16 schema: edges are dicts with 'from'/'to'/'port'
    assert isinstance(edges[0], dict), (
        f"Expected dict edges (2026-05-16 schema), got {type(edges[0]).__name__}: {edges[0]}"
    )
    assert "from" in edges[0] and "to" in edges[0], edges[0]


def test_metlinkr_fixture_has_expected_layout():
    root = FIXTURE_ROOT / "metlinkr_subset"
    assert root.exists(), f"Secondary 2026-05-15 fixture missing: {root}"
    skill_dirs = [p for p in (root / "skills").iterdir() if p.is_dir()]
    assert len(skill_dirs) == 1, f"Expected 1 skill, got {skill_dirs}"
    card_jsons = sorted((root / "cards").glob("task_*.json"))
    assert len(card_jsons) == 2, f"Expected 2 card JSON files, got {card_jsons}"
    # 2026-05-15 schema: edges may be lists (pairs)
    dag_path = root / "workflow_dag.json"
    dag = json.loads(dag_path.read_text())
    edges = dag.get("edges", [])
    assert edges, "workflow_dag.json has no edges"
    # Old schema: edges are 2-element lists/tuples
    first = edges[0]
    assert isinstance(first, (list, tuple)) and len(first) == 2, (
        f"Expected 2-element list/tuple edges (2026-05-15 schema), got {first}"
    )
