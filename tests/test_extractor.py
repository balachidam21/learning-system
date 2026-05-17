import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from extractor import extract_session, ExtractorResult, _robust_json_parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _mock_cli_response(signal_payload: dict, input_tokens=500, output_tokens=80):
    """Build a fake `claude -p --output-format=json` stdout."""
    outer = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": f"```json\n{json.dumps(signal_payload)}\n```",
        "duration_ms": 1200,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "total_cost_usd": 0.002,
        "session_id": "fake-sess",
        "uuid": "fake-uuid",
    }
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(outer)
    proc.stderr = ""
    return proc


def test_extract_tutoring_session():
    expected = json.loads((FIXTURES / "expected/tutoring_session.json").read_text())
    transcript = FIXTURES / "tutoring_session.jsonl"
    with patch("extractor.subprocess.run", return_value=_mock_cli_response(expected, input_tokens=1200)):
        result = extract_session(transcript)
    assert isinstance(result, ExtractorResult)
    assert result.signal["session_id"] == "f4a8-tut-001"
    assert "KL asymmetry" in result.signal["topics"]
    assert result.lineage["extractor_version"]
    assert result.lineage["prompt_id"] == "extract.v1"
    assert result.lineage["tokens_in"] == 1200
    assert result.lineage["backend"] == "claude-cli"


def test_extract_logging_session():
    expected = json.loads((FIXTURES / "expected/logging_session.json").read_text())
    transcript = FIXTURES / "logging_session.jsonl"
    with patch("extractor.subprocess.run", return_value=_mock_cli_response(expected)):
        result = extract_session(transcript)
    assert result.signal["topics"] == ["cross-entropy as likelihood"]
    assert result.signal["patch_list_deltas_inferred"][0]["to"] == "🟢"


def test_extract_handles_cli_nonzero_exit():
    transcript = FIXTURES / "tutoring_session.jsonl"
    bad = MagicMock()
    bad.returncode = 1
    bad.stdout = ""
    bad.stderr = "simulated CLI failure"
    with patch("extractor.subprocess.run", return_value=bad):
        result = extract_session(transcript)
    assert result.signal["extraction_status"] == "failed"
    assert "simulated CLI failure" in result.signal["error"]


def test_extract_handles_non_json_response():
    """CLI succeeds but model returns prose instead of JSON."""
    transcript = FIXTURES / "tutoring_session.jsonl"
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "I cannot extract from this transcript.",
        "duration_ms": 100, "usage": {"input_tokens": 50, "output_tokens": 10},
        "total_cost_usd": 0.001, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    with patch("extractor.subprocess.run", return_value=proc):
        result = extract_session(transcript)
    assert result.signal["extraction_status"] == "malformed"
    assert "raw_response" in result.signal


def test_extract_handles_cli_error_field():
    """CLI returns valid outer JSON but is_error=true."""
    transcript = FIXTURES / "tutoring_session.jsonl"
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({
        "type": "result", "subtype": "rate_limit",
        "is_error": True,
        "result": "", "duration_ms": 100, "usage": {},
        "total_cost_usd": 0.0, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    with patch("extractor.subprocess.run", return_value=proc):
        result = extract_session(transcript)
    assert result.signal["extraction_status"] == "failed"


def test_extract_skips_oversized_transcript(tmp_path):
    """Transcripts over MAX_TRANSCRIPT_BYTES should skip without calling the CLI."""
    import extractor as ext
    oversized = tmp_path / "huge.jsonl"
    oversized.write_text("x" * (ext.MAX_TRANSCRIPT_BYTES + 1))

    # If subprocess.run is called, the test fails — guard should short-circuit.
    with patch("extractor.subprocess.run") as mock_run:
        result = extract_session(oversized)
        mock_run.assert_not_called()

    assert result.signal["extraction_status"] == "skipped_too_large"
    assert "exceeds MAX_TRANSCRIPT_BYTES" in result.signal["error"]


def test_chunk_transcript_splits_on_line_boundaries():
    from extractor import _chunk_transcript
    # 10 lines of ~100 bytes each = ~1000 bytes total. chunk at 300 bytes → ~3-4 chunks.
    text = "\n".join("x" * 99 for _ in range(10)) + "\n"
    chunks = _chunk_transcript(text, chunk_bytes=300)
    assert len(chunks) >= 3
    assert "".join(chunks) == text  # lossless reconstitution
    for c in chunks:
        for line in c.splitlines(keepends=True):
            # every line ends with \n (no partial line at chunk boundary)
            assert line.endswith("\n") or line == c.splitlines(keepends=True)[-1]


def test_chunk_transcript_handles_single_long_line():
    """A single line exceeding chunk_bytes goes into its own chunk."""
    from extractor import _chunk_transcript
    text = "x" * 500 + "\n" + "y" * 50 + "\n"
    chunks = _chunk_transcript(text, chunk_bytes=200)
    assert "".join(chunks) == text
    assert len(chunks) == 2   # long line gets its own chunk, then the short one


def test_merge_signals_unions_topics_concats_markers():
    from extractor import _merge_signals
    s1 = {"topics": ["A", "B"], "click_markers": ["m1"], "extraction_status": "ok",
          "started_at": "2026-05-10T15:00:00Z", "ended_at": "2026-05-10T15:30:00Z",
          "duration_min": 30}
    s2 = {"topics": ["B", "C"], "click_markers": ["m2"], "extraction_status": "ok",
          "started_at": "2026-05-10T15:30:00Z", "ended_at": "2026-05-10T16:00:00Z",
          "duration_min": 30}
    out = _merge_signals([s1, s2], "test-session")
    assert sorted(out["topics"]) == ["A", "B", "C"]
    assert out["click_markers"] == ["m1", "m2"]
    assert out["started_at"] == "2026-05-10T15:00:00Z"
    assert out["ended_at"] == "2026-05-10T16:00:00Z"
    assert out["duration_min"] == 60
    assert out["chunked"] is True
    assert out["chunk_count"] == 2


def test_extract_chunks_oversized_transcript(tmp_path, monkeypatch):
    """Transcript larger than SINGLE_SHOT_MAX_BYTES triggers chunked extraction."""
    from extractor import SINGLE_SHOT_MAX_BYTES, MAX_CHUNKS
    big = tmp_path / "big.jsonl"
    # Make a transcript just over SINGLE_SHOT_MAX_BYTES, so it splits into 2-3 chunks
    line = '{"type":"user","content":"' + "x" * 200 + '"}\n'
    target_size = SINGLE_SHOT_MAX_BYTES + 100_000
    num_lines = target_size // len(line) + 1
    big.write_text(line * num_lines)
    assert big.stat().st_size > SINGLE_SHOT_MAX_BYTES

    # Mock subprocess.run to return ok for each chunk with distinct topics
    call_count = {"n": 0}
    def fake_run(*a, **kw):
        call_count["n"] += 1
        payload = {
            "topics": [f"topic-{call_count['n']}"],
            "extraction_status": "ok",
        }
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = json.dumps({
            "type": "result", "is_error": False, "result": json.dumps(payload),
            "duration_ms": 100, "usage": {"input_tokens": 200, "output_tokens": 50},
            "total_cost_usd": 0.001, "session_id": "x", "uuid": "y",
        })
        proc.stderr = ""
        return proc

    with patch("extractor.subprocess.run", side_effect=fake_run):
        result = extract_session(big)

    assert call_count["n"] >= 2  # at least 2 chunks → at least 2 CLI calls
    assert result.signal["extraction_status"] == "ok"
    assert result.signal["chunked"] is True
    assert result.signal["chunk_count"] >= 2
    assert len(result.signal["topics"]) >= 2  # union of per-chunk topics
    assert result.lineage["chunked"] is True


def test_extract_skips_beyond_max_chunks(tmp_path):
    """Transcripts requiring more than MAX_CHUNKS chunks should skip cleanly."""
    from extractor import MAX_CHUNKS, CHUNK_BYTES
    huge = tmp_path / "huge.jsonl"
    # Just over MAX_CHUNKS * CHUNK_BYTES
    huge.write_text("x" * (MAX_CHUNKS * CHUNK_BYTES + 10_000))
    with patch("extractor.subprocess.run") as mock_run:
        result = extract_session(huge)
        mock_run.assert_not_called()  # never even attempts
    assert result.signal["extraction_status"] == "skipped_too_large"


def test_robust_parse_bare():
    assert _robust_json_parse('{"a": 1}') == {"a": 1}

def test_robust_parse_fenced():
    assert _robust_json_parse('```json\n{"a": 1}\n```') == {"a": 1}

def test_robust_parse_prose_wrapped():
    txt = 'Here is the extraction:\n{"a": 1, "b": [2]}\nDone.'
    assert _robust_json_parse(txt) == {"a": 1, "b": [2]}

def test_robust_parse_garbage_returns_none():
    assert _robust_json_parse("I cannot do that.") is None
    assert _robust_json_parse("") is None

def test_robust_parse_rejects_non_dict():
    assert _robust_json_parse('[1, 2, 3]') is None


def test_robust_parse_fenced_with_prose_both_sides():
    txt = 'Sure, here is the result:\n```json\n{"a": 1}\n```\nLet me know if you need more.'
    assert _robust_json_parse(txt) == {"a": 1}


def test_robust_parse_multiple_objects_returns_none():
    # documents actual behavior: first-{ to last-} spans both → invalid → None
    assert _robust_json_parse('{"first": 1} {"second": 2}') is None
