"""Unit tests: recency weighting + provenance events wired into LiteratureSurveyRAGMode."""



def test_survey_imports_recency():
    from perspicacite.rag.modes import literature_survey as m
    src = open(m.__file__).read()
    assert "apply_recency_weighting" in src  # chunk or paper variant


def test_survey_imports_get_collector():
    from perspicacite.rag.modes import literature_survey as m
    src = open(m.__file__).read()
    assert "get_collector" in src
    assert "add_trace" in src


def test_survey_has_stage_label():
    from perspicacite.rag.modes import literature_survey as m
    src = open(m.__file__).read()
    assert "stage=\"survey." in src or "stage='survey." in src
