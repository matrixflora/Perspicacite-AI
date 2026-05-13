from perspicacite.pipeline.parsers.figure_context import FigureContext
from perspicacite.rag.multimodal import build_messages_with_figures


def _fc(fid="pdf_p1_i0", b64="AAAA"):
    return FigureContext(
        figure_id=fid, label="Figure 1", caption="cap", source="pdf",
        image_b64=b64, filename="fig_p001_i00.png",
    )


def test_disabled_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="claude-3-5-sonnet",
        config_enabled=False, max_images=6,
    )
    assert out is base


def test_non_vision_model_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="deepseek-chat",
        config_enabled=True, max_images=6,
    )
    assert out is base


def test_no_loaded_images_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc(b64=None)], model="claude-3-5-sonnet",
        config_enabled=True, max_images=6,
    )
    assert out is base


def test_vision_path_injects_block_and_images():
    base = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Explain Figure 1."},
    ]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="claude-3-5-sonnet",
        config_enabled=True, max_images=6,
    )
    assert out[0]["role"] == "system"
    assert "Available figures" in out[0]["content"]
    assert "figure_id" in out[0]["content"]
    user_msg = out[-1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    types = [p["type"] for p in user_msg["content"]]
    assert "text" in types and "image_url" in types
