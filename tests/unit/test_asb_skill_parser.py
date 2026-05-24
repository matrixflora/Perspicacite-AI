from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"


def test_parse_skill_bundle_finds_one_skill():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skills = parse_skill_bundle(FIXTURE)
    assert len(skills) == 1
    assert skills[0].slug == "cross-identifier-reconciliation"
    assert "metabolite" in skills[0].description.lower()


def test_parse_skill_bundle_extracts_tools():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    tool_names = {t.name for t in skill.tools}
    assert "MetLinkR" in tool_names


def test_parse_skill_bundle_extracts_parameters_and_environments():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    assert isinstance(skill.parameters, list)
    assert isinstance(skill.environments, list)
    # at least one env named (language field set)
    assert any(env.language for env in skill.environments)


def test_parse_skill_bundle_loads_papers_and_links():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    # MetLinkR has at least one backing paper DOI
    dois = [p.doi for p in skill.papers if p.doi]
    assert any(doi.startswith("10.1021/") for doi in dois)
    # links.json has at least one entry
    assert isinstance(skill.links, list)
    assert len(skill.links) >= 1


def test_parse_skill_bundle_includes_body_text():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    skill = parse_skill_bundle(FIXTURE)[0]
    assert skill.body_markdown  # non-empty
    body_lower = skill.body_markdown.lower()
    assert "refmet" in body_lower or "metabolite" in body_lower


def test_parse_skill_bundle_missing_index_raises():
    from perspicacite.pipeline.asb.skill_parser import parse_skill_bundle
    with pytest.raises(FileNotFoundError):
        parse_skill_bundle("/tmp/__no_such_asb_run__")
