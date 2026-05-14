import json

from perspicacite.pipeline.external.notebooks import strip_notebook_outputs


def test_strips_outputs_and_execution_count():
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print('hi')"],
                "execution_count": 5,
                "outputs": [
                    {"output_type": "stream", "text": "hi\n"},
                    {"output_type": "display_data", "data": {"image/png": "base64..."}},
                ],
            },
            {
                "cell_type": "markdown",
                "source": ["# Title"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    out_raw = strip_notebook_outputs(json.dumps(nb))
    out = json.loads(out_raw)
    code_cell = out["cells"][0]
    assert code_cell["outputs"] == []
    assert code_cell["execution_count"] is None
    # source preserved
    assert code_cell["source"] == ["print('hi')"]
    # markdown unchanged
    assert out["cells"][1]["source"] == ["# Title"]


def test_invalid_json_passthrough():
    assert strip_notebook_outputs("not valid json {") == "not valid json {"


def test_empty_cells_list():
    out = json.loads(strip_notebook_outputs(json.dumps({"cells": []})))
    assert out["cells"] == []


def test_drops_image_payloads_bytes_reduction():
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["x = 1"],
                "execution_count": 1,
                "outputs": [{"output_type": "display_data", "data": {"image/png": "X" * 5000}}],
            }
        ],
    }
    src = json.dumps(nb)
    out = strip_notebook_outputs(src)
    assert len(out) < len(src) / 2
    assert "X" * 100 not in out
