"""Tests for ASB-Skill collection v1 parser."""
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "skill_collection_v1"


def test_parse_skill_collection_finds_skills():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    assert len(result.skills) == 1
    assert result.skills[0].slug == "feature-detection-lcms"


def test_parse_skill_collection_extracts_overview_section():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    assert skill.overview_chunk
    assert "MZmine" in skill.overview_chunk or "Feature detection" in skill.overview_chunk


def test_parse_skill_collection_extracts_procedure_section():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    assert skill.procedure_chunk
    assert "Load raw mzML" in skill.procedure_chunk or "mzML" in skill.procedure_chunk


def test_parse_skill_collection_extracts_tools():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    # tools come from tools.lock.yaml resolved against tools/*.yaml
    assert len(skill.tools_chunk) > 0
    assert "MZmine" in skill.tools_chunk or "mzmine" in skill.tools_chunk.lower()


def test_parse_skill_collection_extracts_dois():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    assert "10.1021/acs.jproteome.0c00920" in skill.derived_from_dois


def test_parse_skill_collection_extracts_edam_iris():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    # collection-level EDAM topics come from collection.yaml
    assert "http://edamontology.org/topic_3172" in result.edam_topics


def test_parse_skill_collection_reads_catalogue():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    assert result.catalogue_entries
    assert any(e.get("name") == "feature-detection-lcms" for e in result.catalogue_entries)


def test_parse_skill_collection_missing_collection_yaml_raises():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    with pytest.raises(FileNotFoundError):
        parse_skill_collection(Path("/tmp/__no_collection_v1__"))


def test_parse_skill_collection_skill_edam_operation():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    assert skill.edam_operation == "http://edamontology.org/operation_3215"


def test_parse_skill_collection_skill_edam_topics():
    from perspicacite.pipeline.asb.collection_parser import parse_skill_collection
    result = parse_skill_collection(FIXTURE)
    skill = result.skills[0]
    assert "http://edamontology.org/topic_3172" in skill.edam_topics
