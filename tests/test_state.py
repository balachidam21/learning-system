from pathlib import Path
from lib.state import load_state, save_state, needs_extraction

def test_load_state_returns_empty_when_missing(tmp_path):
    state_path = tmp_path / "state.json"
    s = load_state(state_path)
    assert s == {"schema_version": 1, "extractor_version": None, "sessions": {}}

def test_save_then_load_roundtrip(tmp_path):
    state_path = tmp_path / "state.json"
    s = {"schema_version": 1, "extractor_version": "0.1.0",
         "sessions": {"abc": {"last_mtime": "2026-05-12T21:05:00Z"}}}
    save_state(state_path, s)
    assert load_state(state_path) == s

def test_needs_extraction_when_no_prior_record(tmp_path):
    state = {"schema_version": 1, "extractor_version": "0.1.0", "sessions": {}}
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("dummy")
    assert needs_extraction(transcript, state, current_version="0.1.0") is True

def test_needs_extraction_when_version_bumped(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("dummy")
    sid = transcript.stem
    state = {
        "schema_version": 1, "extractor_version": "0.1.0",
        "sessions": {sid: {"last_mtime": "2099-01-01T00:00:00Z"}}
    }
    assert needs_extraction(transcript, state, current_version="0.2.0") is True

def test_no_extraction_needed_when_unchanged(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("dummy")
    import datetime
    mtime = datetime.datetime.utcfromtimestamp(transcript.stat().st_mtime).isoformat() + "Z"
    state = {
        "schema_version": 1, "extractor_version": "0.1.0",
        "sessions": {transcript.stem: {"last_mtime": mtime}}
    }
    assert needs_extraction(transcript, state, current_version="0.1.0") is False
