"""build_asb_response_metadata: derive skill / workflow summary
blocks from chunk dicts. Pure function — no I/O."""


def test_build_asb_response_metadata_groups_skills():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {
            "content_kind": "skill_body", "skill_id": "abc",
            "skill_name": "Abc",
            "tools": [{"name": "T1", "canonical_url": "u1", "install": "pip install t1"}],
            "environment": [{"language": "R"}],
            "parameters": [],
        }},
        # Duplicate skill_id → coalesced
        {"metadata": {
            "content_kind": "skill_body", "skill_id": "abc",
            "skill_name": "Abc", "tools": [], "environment": [], "parameters": [],
        }},
        {"metadata": {
            "content_kind": "skill_body", "skill_id": "xyz",
            "skill_name": "Xyz", "tools": [], "environment": [], "parameters": [],
        }},
    ]
    out = build_asb_response_metadata(chunks)
    assert {s["skill_id"] for s in out["skill_metadata"]} == {"abc", "xyz"}
    assert out["workflow_metadata"] == []


def test_build_asb_response_metadata_skill_executable_flag():
    """A skill is 'executable' iff every tool has canonical_url + install."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    # All tools installable → executable
    out = build_asb_response_metadata([{
        "metadata": {
            "content_kind": "skill_body", "skill_id": "s",
            "skill_name": "S",
            "tools": [
                {"name": "T1", "canonical_url": "u1", "install": "pip install t1"},
                {"name": "T2", "canonical_url": "u2", "install": "brew install t2"},
            ],
            "environment": [], "parameters": [],
        }
    }])
    assert out["skill_metadata"][0]["executable"] is True

    # One tool missing install → not executable
    out = build_asb_response_metadata([{
        "metadata": {
            "content_kind": "skill_body", "skill_id": "s",
            "skill_name": "S",
            "tools": [
                {"name": "T1", "canonical_url": "u1", "install": "pip install t1"},
                {"name": "T2", "canonical_url": "u2", "install": None},
            ],
            "environment": [], "parameters": [],
        }
    }])
    assert out["skill_metadata"][0]["executable"] is False

    # No tools → not executable
    out = build_asb_response_metadata([{
        "metadata": {
            "content_kind": "skill_body", "skill_id": "s",
            "skill_name": "S",
            "tools": [], "environment": [], "parameters": [],
        }
    }])
    assert out["skill_metadata"][0]["executable"] is False


def test_build_asb_response_metadata_includes_asb_mcp_hint():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata
    out = build_asb_response_metadata([{
        "metadata": {
            "content_kind": "skill_body", "skill_id": "my-skill",
            "skill_name": "My", "tools": [], "environment": [], "parameters": [],
        }
    }])
    assert out["skill_metadata"][0]["asb_mcp_hint"] == "asb://skill/my-skill"


def test_build_asb_response_metadata_groups_workflows():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {
            "content_kind": "workflow_card", "task_id": "task_001",
            "task_card_title": "T1", "domain": "metabolomics",
            "skills_used": ["s1"], "tools_used": ["T"],
            "parameters": [], "expected_outputs": [],
            "evaluation_strategy": {}, "paper_doi": "10.x/y",
            "paper_github": "org/repo",
            "downstream_tasks": ["task_002"], "upstream_tasks": [],
        }},
    ]
    out = build_asb_response_metadata(chunks)
    assert out["skill_metadata"] == []
    assert len(out["workflow_metadata"]) == 1
    wm = out["workflow_metadata"][0]
    assert wm["task_id"] == "task_001"
    assert wm["downstream_tasks"] == ["task_002"]
    assert wm["paper_doi"] == "10.x/y"


def test_build_asb_response_metadata_workflow_carries_2026_05_16_fields():
    """workflow_metadata must surface task_objective, executable dict,
    execution_profile, task_inputs/outputs, expected_artifact_name,
    run_timeout_seconds, reproducibility_tier."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [{
        "metadata": {
            "content_kind": "workflow_card", "task_id": "t1",
            "task_card_title": "T1",
            "task_objective": "Repair structures",
            "executable": {"cmd": ["python", "-m", "asb.tasks.repair"], "env": {"X": "1"}},
            "execution_profile": {"compute_tier": "fast"},
            "task_inputs": [{"port": "library", "type": "msp"}],
            "task_outputs": [{"port": "repaired", "type": "msp"}],
            "expected_artifact_name": "repaired_library.msp",
            "run_timeout_seconds": 600.0,
            "reproducibility_tier": "deterministic",
        }
    }]
    wm = build_asb_response_metadata(chunks)["workflow_metadata"][0]
    assert wm["task_objective"] == "Repair structures"
    assert wm["executable"] == {"cmd": ["python", "-m", "asb.tasks.repair"], "env": {"X": "1"}}
    assert wm["execution_profile"] == {"compute_tier": "fast"}
    assert wm["task_inputs"] == [{"port": "library", "type": "msp"}]
    assert wm["task_outputs"] == [{"port": "repaired", "type": "msp"}]
    assert wm["expected_artifact_name"] == "repaired_library.msp"
    assert wm["run_timeout_seconds"] == 600.0
    assert wm["reproducibility_tier"] == "deterministic"


def test_build_asb_response_metadata_mixed_and_unrelated_chunks():
    """Chunks without content_kind are ignored. Mixed input works."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": {}},                                              # ignored
        {"metadata": {"content_kind": "skill_body", "skill_id": "s",
                      "skill_name": "S", "tools": [], "environment": [], "parameters": []}},
        {"metadata": {"content_kind": "workflow_card", "task_id": "t",
                      "task_card_title": "T"}},
    ]
    out = build_asb_response_metadata(chunks)
    assert len(out["skill_metadata"]) == 1
    assert len(out["workflow_metadata"]) == 1


def test_build_asb_response_metadata_handles_missing_metadata():
    """Chunks without a 'metadata' key are ignored, not crashed-on."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata
    out = build_asb_response_metadata([{}])
    assert out == {"skill_metadata": [], "workflow_metadata": []}


def test_build_asb_response_metadata_empty_input():
    from perspicacite.pipeline.asb.response import build_asb_response_metadata
    assert build_asb_response_metadata([]) == {
        "skill_metadata": [],
        "workflow_metadata": [],
    }


def test_build_asb_response_metadata_skips_non_dict_metadata():
    """A malformed upstream may pass metadata as a string, SimpleNamespace,
    or other non-dict — the helper must skip silently rather than
    raising AttributeError on ``md.get(...)``."""
    from types import SimpleNamespace

    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    chunks = [
        {"metadata": "not-a-dict"},
        {"metadata": SimpleNamespace(content_kind="skill_body", skill_id="x")},
        {"metadata": 42},
        {"metadata": ["list", "instead"]},
        "not-even-a-chunk",  # non-dict chunk
        # Mixed: one valid + four invalid
        {"metadata": {"content_kind": "skill_body", "skill_id": "ok",
                      "skill_name": "Ok", "tools": [], "environment": [],
                      "parameters": []}},
    ]
    out = build_asb_response_metadata(chunks)
    assert [s["skill_id"] for s in out["skill_metadata"]] == ["ok"]
    assert out["workflow_metadata"] == []


def test_build_asb_response_metadata_skips_non_dict_tool_entries():
    """If a chunk's metadata.tools contains a bare string (or other
    non-dict), the helper must skip those entries rather than crashing
    on ``t.get('canonical_url')``."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    out = build_asb_response_metadata([{
        "metadata": {
            "content_kind": "skill_body", "skill_id": "s",
            "skill_name": "S",
            "tools": [
                "https://bare-url-not-a-dict",
                {"name": "ok", "canonical_url": "u", "install": "pip install ok"},
            ],
            "environment": [], "parameters": [],
        }
    }])
    sm = out["skill_metadata"][0]
    # Only the dict entry survives into tool_requirements
    assert sm["tool_requirements"] == [
        {"name": "ok", "canonical_url": "u", "install": "pip install ok"}
    ]
    # And executable is True because the surviving tool has both fields
    assert sm["executable"] is True
