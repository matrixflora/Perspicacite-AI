"""Unit tests for EDAM pre-filter helper."""


def test_edam_filter_returns_all_when_no_criteria():
    from perspicacite.pipeline.asb.edam_filter import edam_pre_filter

    chunks = [
        {"paper_id": "a", "metadata": {"edam_operation": "http://edamontology.org/operation_3215", "edam_topics": ["http://edamontology.org/topic_3172"]}},
        {"paper_id": "b", "metadata": {"edam_operation": "http://edamontology.org/operation_0004", "edam_topics": []}},
        {"paper_id": "c", "metadata": {}},
    ]
    result = edam_pre_filter(chunks, edam_operation=None, edam_topics=None)
    assert len(result) == 3


def test_edam_filter_by_operation():
    from perspicacite.pipeline.asb.edam_filter import edam_pre_filter

    chunks = [
        {"paper_id": "a", "metadata": {"edam_operation": "http://edamontology.org/operation_3215", "edam_topics": []}},
        {"paper_id": "b", "metadata": {"edam_operation": "http://edamontology.org/operation_0004", "edam_topics": []}},
        {"paper_id": "c", "metadata": {}},
    ]
    result = edam_pre_filter(
        chunks,
        edam_operation="http://edamontology.org/operation_3215",
        edam_topics=None,
    )
    # Only chunk a matches; c (no metadata EDAM) should also pass through
    # (fallback: chunks with no EDAM metadata pass through to avoid over-filtering)
    ids = [r["paper_id"] for r in result]
    assert "a" in ids
    assert "b" not in ids
    assert "c" in ids  # no EDAM metadata → pass through


def test_edam_filter_by_topic():
    from perspicacite.pipeline.asb.edam_filter import edam_pre_filter

    target = "http://edamontology.org/topic_3172"
    chunks = [
        {"paper_id": "a", "metadata": {"edam_topics": [target, "http://edamontology.org/topic_0091"]}},
        {"paper_id": "b", "metadata": {"edam_topics": ["http://edamontology.org/topic_0004"]}},
        {"paper_id": "c", "metadata": {"edam_topics": []}},
        {"paper_id": "d", "metadata": {}},
    ]
    result = edam_pre_filter(
        chunks,
        edam_operation=None,
        edam_topics=[target],
    )
    ids = [r["paper_id"] for r in result]
    assert "a" in ids
    assert "b" not in ids
    assert "c" not in ids  # has topics field but no match
    assert "d" in ids  # no EDAM metadata → pass through


def test_edam_filter_combined_operation_and_topic():
    from perspicacite.pipeline.asb.edam_filter import edam_pre_filter

    target_op = "http://edamontology.org/operation_3215"
    target_topic = "http://edamontology.org/topic_3172"
    chunks = [
        {"paper_id": "a", "metadata": {"edam_operation": target_op, "edam_topics": [target_topic]}},
        {"paper_id": "b", "metadata": {"edam_operation": target_op, "edam_topics": ["http://edamontology.org/topic_0004"]}},
        {"paper_id": "c", "metadata": {"edam_operation": "http://edamontology.org/operation_0004", "edam_topics": [target_topic]}},
        {"paper_id": "d", "metadata": {}},
    ]
    # With both criteria: must match operation AND at least one topic
    result = edam_pre_filter(
        chunks,
        edam_operation=target_op,
        edam_topics=[target_topic],
    )
    ids = [r["paper_id"] for r in result]
    assert "a" in ids   # matches both
    assert "b" not in ids   # op matches, topic doesn't
    assert "c" not in ids   # topic matches, op doesn't
    assert "d" in ids   # no metadata → pass through


def test_edam_filter_empty_input():
    from perspicacite.pipeline.asb.edam_filter import edam_pre_filter
    result = edam_pre_filter(
        [], edam_operation="http://edamontology.org/operation_3215", edam_topics=None
    )
    assert result == []
