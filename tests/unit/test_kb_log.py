"""Tests for KBLogWriter + KBEvent (Wave 4.3)."""
import json
import threading
from pathlib import Path

from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter


def _writer(tmp_path: Path) -> KBLogWriter:
    return KBLogWriter(path=tmp_path / "kb.jsonl")


def _event(kind: str = "paper_added", paper_id: str = "10.1/a", **kw) -> KBEvent:
    return KBEvent(
        event=kind, kb_name="kb1", paper_id=paper_id, **kw,
    )


def test_append_writes_one_line_per_event(tmp_path):
    w = _writer(tmp_path)
    w.append(_event("paper_added"))
    w.append(_event("paper_skipped", paper_id="10.2/b"))
    content = (tmp_path / "kb.jsonl").read_text()
    lines = content.strip().split("\n")
    assert len(lines) == 2
    # Each line must be valid JSON.
    json.loads(lines[0])
    json.loads(lines[1])


def test_read_all_returns_events_in_order(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a"))
    w.append(_event(paper_id="b"))
    w.append(_event(paper_id="c"))
    events = w.read_all()
    assert [e.paper_id for e in events] == ["a", "b", "c"]


def test_read_all_on_missing_file_returns_empty(tmp_path):
    w = _writer(tmp_path)
    assert w.read_all() == []


def test_partial_line_at_eof_silently_skipped(tmp_path):
    """A SIGKILL mid-write may leave half a line — reader must
    tolerate it on the LAST line only."""
    p = tmp_path / "kb.jsonl"
    p.write_text(
        json.dumps({"ts": 1, "event": "paper_added", "kb_name": "kb1",
                    "paper_id": "10.1/a"}) + "\n"
        + '{"partial":'  # broken trailing fragment
    )
    w = KBLogWriter(path=p)
    events = w.read_all()
    assert len(events) == 1
    assert events[0].paper_id == "10.1/a"


def test_malformed_middle_line_logged_and_skipped(tmp_path):
    p = tmp_path / "kb.jsonl"
    p.write_text(
        json.dumps({"ts": 1, "event": "paper_added", "kb_name": "kb1",
                    "paper_id": "10.1/a"}) + "\n"
        + "not-json-junk\n"
        + json.dumps({"ts": 2, "event": "paper_added", "kb_name": "kb1",
                      "paper_id": "10.2/b"}) + "\n"
    )
    w = KBLogWriter(path=p)
    events = w.read_all()
    assert len(events) == 2
    assert events[0].paper_id == "10.1/a"
    assert events[1].paper_id == "10.2/b"


def test_read_after_filters_by_ts(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a", ts=100))
    w.append(_event(paper_id="b", ts=200))
    w.append(_event(paper_id="c", ts=300))
    recent = w.read_after(ts=150)
    assert [e.paper_id for e in recent] == ["b", "c"]


def test_concurrent_appends_dont_interleave(tmp_path):
    """20 threads × 50 appends each = 1000 events; each line must
    still be valid JSON after the smoke."""
    w = _writer(tmp_path)

    def hammer():
        for i in range(50):
            w.append(_event(paper_id=f"p-{threading.get_ident()}-{i}"))

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "kb.jsonl").read_text().strip().split("\n")
    assert len(lines) == 20 * 50
    for ln in lines:
        json.loads(ln)  # raises if any line is corrupt


def test_rollback_after_returns_paper_ids(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a", ts=100))
    w.append(_event(paper_id="b", ts=200))
    w.append(_event(paper_id="c", ts=300))
    w.append(_event("paper_skipped", paper_id="d", ts=250))

    rolled = w.rollback_after(ts=150)
    # Only paper_added events count for rollback, not skipped.
    assert set(rolled) == {"b", "c"}

    # A kb_pruned event should have been recorded after the rollback.
    events = w.read_all()
    pruned = [e for e in events if e.event == "kb_pruned"]
    assert len(pruned) == 1


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    """A disk-full / permission error must NOT propagate — provenance
    is best-effort. Caller's ingest loop keeps going."""
    w = _writer(tmp_path)

    def boom(*a, **kw):
        raise PermissionError("read-only fs")

    monkeypatch.setattr("builtins.open", boom)
    # Should not raise.
    w.append(_event())
