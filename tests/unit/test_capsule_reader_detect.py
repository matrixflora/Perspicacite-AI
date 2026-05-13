import json

from perspicacite.integrations.capsule_reader import is_capsule_dir


def test_capsule_with_metadata_version_is_detected(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({
        "capsule_version": "0.1", "paper_id": "p"
    }))
    assert is_capsule_dir(tmp_path) is True


def test_no_metadata_is_not_capsule(tmp_path):
    assert is_capsule_dir(tmp_path) is False


def test_metadata_without_version_not_capsule(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"paper_id": "p"}))
    assert is_capsule_dir(tmp_path) is False


def test_corrupt_metadata_not_capsule(tmp_path):
    (tmp_path / "metadata.json").write_text("{not valid json")
    assert is_capsule_dir(tmp_path) is False


def test_non_dir_input_handled(tmp_path):
    f = tmp_path / "not_a_dir.json"
    f.write_text("{}")
    assert is_capsule_dir(f) is False
