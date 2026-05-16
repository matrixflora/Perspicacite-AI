"""End-to-end orchestrator test. KB layer + DOI ingest are mocked
at the orchestrator's seam points; the test asserts the flow:
parsers run → Papers built → KB receives Papers → skill_kb.json
updated → DAG stored on KB description.
"""
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

METLINKR = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"
ARTICLE = Path(__file__).parent.parent / "fixtures" / "asb" / "article_878_v4_subset"


@pytest.mark.asyncio
async def test_ingest_asb_run_composite_mode_metlinkr(tmp_path):
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(METLINKR, target)

    added_papers: list = []
    fake_kb = MagicMock()
    fake_kb.name = "metlinkr_bundle"
    fake_kb.description = ""

    async def fake_add_papers(papers, **kw):
        added_papers.extend(papers)
        return len(papers)

    fake_kb.add_papers = fake_add_papers

    async def fake_make_or_get_kb(name, description="", **kw):
        fake_kb.name = name
        fake_kb.description = description
        return fake_kb

    async def fake_ingest_backing_dois(*, kb, dois, app_state):
        return {"added": len(dois), "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest._ingest_backing_paper_dois",
        side_effect=fake_ingest_backing_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="metlinkr_bundle",
            include=("skills", "workflows"),
            mode="composite",
            app_state=None,
        )

    assert result["kb_names"] == ["metlinkr_bundle"]
    assert result["skills_ingested"] == 1
    assert result["workflows_ingested"] == 2
    assert result["papers_ingested"] >= 1
    paper_ids = {p.id for p in added_papers}
    assert "asb_skill:cross-identifier-reconciliation" in paper_ids
    assert "asb_card:task_001" in paper_ids
    assert "asb_card:task_002" in paper_ids
    # DAG stored on KB description (JSON-encoded under "workflow_dag" key)
    assert "workflow_dag" in (fake_kb.description or "")
    # skill_kb.json updated
    sk = json.loads(
        (target / "skills" / "cross-identifier-reconciliation"
         / "skill_kb.json").read_text()
    )
    assert sk["entries"]


@pytest.mark.asyncio
async def test_ingest_asb_run_composite_mode_article(tmp_path):
    """Same flow, 2026-05-16 fixture: 2 skills, 3 cards, dict-edge DAG."""
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(ARTICLE, target)

    added_papers: list = []
    fake_kb = MagicMock()
    fake_kb.name = "article_bundle"
    fake_kb.description = ""

    async def fake_add_papers(papers, **kw):
        added_papers.extend(papers)
        return len(papers)

    fake_kb.add_papers = fake_add_papers

    async def fake_make_or_get_kb(name, description="", **kw):
        fake_kb.name = name
        fake_kb.description = description
        return fake_kb

    async def fake_ingest_backing_dois(*, kb, dois, app_state):
        return {"added": len(dois), "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest._ingest_backing_paper_dois",
        side_effect=fake_ingest_backing_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="article_bundle",
            include=("skills", "workflows"),
            mode="composite",
            app_state=None,
        )

    assert result["skills_ingested"] == 2
    assert result["workflows_ingested"] == 3
    paper_ids = {p.id for p in added_papers}
    assert "asb_skill:mass-spectral-library-curation" in paper_ids
    assert "asb_skill:chemical-structure-annotation-repair" in paper_ids
    assert "asb_card:task_001" in paper_ids
    assert "asb_card:task_002" in paper_ids
    assert "asb_card:task_003" in paper_ids


@pytest.mark.asyncio
async def test_ingest_asb_run_include_skills_only(tmp_path):
    """include=('skills',) skips workflow card ingestion."""
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(METLINKR, target)

    added_papers: list = []
    fake_kb = MagicMock()
    fake_kb.name = "kb"
    fake_kb.description = ""

    async def fake_add_papers(papers, **kw):
        added_papers.extend(papers)
        return len(papers)

    fake_kb.add_papers = fake_add_papers

    async def fake_make_or_get_kb(name, description="", **kw):
        fake_kb.name = name
        return fake_kb

    async def fake_ingest_backing_dois(*, kb, dois, app_state):
        return {"added": 0, "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest._ingest_backing_paper_dois",
        side_effect=fake_ingest_backing_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="kb",
            include=("skills",),
            mode="composite",
            app_state=None,
        )

    assert result["workflows_ingested"] == 0
    paper_ids = {p.id for p in added_papers}
    assert not any(p.startswith("asb_card:") for p in paper_ids)


@pytest.mark.asyncio
async def test_ingest_asb_run_include_workflows_only(tmp_path):
    """include=('workflows',) skips skill ingestion."""
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(METLINKR, target)

    added_papers: list = []
    fake_kb = MagicMock()
    fake_kb.name = "kb"
    fake_kb.description = ""

    async def fake_add_papers(papers, **kw):
        added_papers.extend(papers)
        return len(papers)

    fake_kb.add_papers = fake_add_papers

    async def fake_make_or_get_kb(name, description="", **kw):
        fake_kb.name = name
        return fake_kb

    async def fake_ingest_backing_dois(*, kb, dois, app_state):
        return {"added": 0, "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest._ingest_backing_paper_dois",
        side_effect=fake_ingest_backing_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="kb",
            include=("workflows",),
            mode="composite",
            app_state=None,
        )

    assert result["skills_ingested"] == 0
    paper_ids = {p.id for p in added_papers}
    assert not any(p.startswith("asb_skill:") for p in paper_ids)


@pytest.mark.asyncio
async def test_ingest_asb_run_per_skill_mode(tmp_path):
    """mode='per-skill' creates one KB per skill (plus composite for workflows)."""
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run

    target = tmp_path / "run"
    shutil.copytree(ARTICLE, target)

    kbs_created: list[str] = []
    fake_kbs: dict = {}

    def make_fake_kb(name):
        fake = MagicMock()
        fake.name = name
        fake.description = ""
        fake.added_papers = []

        async def fake_add_papers(papers, **kw):
            fake.added_papers.extend(papers)
            return len(papers)

        fake.add_papers = fake_add_papers
        return fake

    async def fake_make_or_get_kb(name, description="", **kw):
        if name not in fake_kbs:
            fake_kbs[name] = make_fake_kb(name)
            kbs_created.append(name)
        fake_kbs[name].description = description
        return fake_kbs[name]

    async def fake_ingest_backing_dois(*, kb, dois, app_state):
        return {"added": 0, "failed": []}

    with patch(
        "perspicacite.pipeline.asb.run_ingest._make_or_get_kb",
        side_effect=fake_make_or_get_kb,
    ), patch(
        "perspicacite.pipeline.asb.run_ingest._ingest_backing_paper_dois",
        side_effect=fake_ingest_backing_dois,
    ):
        result = await ingest_asb_run(
            asb_run_dir=str(target),
            kb_name="bundle",
            include=("skills", "workflows"),
            mode="per-skill",
            app_state=None,
        )

    # per-skill mode creates: composite "bundle" + one per skill
    assert "bundle" in result["kb_names"]
    assert any("__mass-spectral-library-curation" in n for n in result["kb_names"])
    assert any("__chemical-structure-annotation-repair" in n for n in result["kb_names"])


@pytest.mark.asyncio
async def test_ingest_asb_run_validates_include(tmp_path):
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run
    with pytest.raises(ValueError, match="include"):
        await ingest_asb_run(
            asb_run_dir=str(tmp_path),
            include=(),
            mode="composite",
            app_state=None,
        )


@pytest.mark.asyncio
async def test_ingest_asb_run_validates_mode(tmp_path):
    from perspicacite.pipeline.asb.run_ingest import ingest_asb_run
    with pytest.raises(ValueError, match="mode"):
        await ingest_asb_run(
            asb_run_dir=str(tmp_path),
            include=("skills",),
            mode="bogus",
            app_state=None,
        )
