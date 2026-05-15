import json

from perspicacite.models.rag import StreamEvent


def test_code_excerpt_event_factory():
    ev = StreamEvent.code_excerpt({
        "id": "github:o/r@abc:f.py#L1-L5",
        "language": "python",
        "text": "def fit(): pass",
        "source_url": "https://github.com/o/r/blob/abc/f.py#L1-L5",
    })
    assert ev.event == "code_excerpt"
    payload = json.loads(ev.data)
    assert payload["language"] == "python"


def test_figure_ref_event_factory():
    ev = StreamEvent.figure_ref({
        "id": "pdf_p3_i1",
        "paper_id": "p1",
        "label": "Figure 3",
        "caption": "Test",
    })
    assert ev.event == "figure_ref"
    payload = json.loads(ev.data)
    assert payload["id"] == "pdf_p3_i1"
