import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from extractor import extract_session, ExtractorResult, _robust_json_parse, CallResult, _extract_one_chunk

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
    assert "simulated CLI failure" in result.lineage["error"]
    assert "error" not in result.signal


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
    assert "raw_response" in result.lineage


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


def _outer(result, is_error=False, stop="end_turn", api_err=None, rc=0, stderr=""):
    o = {"is_error": is_error, "result": result, "stop_reason": stop,
         "api_error_status": api_err,
         "usage": {"input_tokens": 10, "output_tokens": 5,
                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
         "total_cost_usd": 0.01}
    p = MagicMock(); p.returncode = rc; p.stdout = json.dumps(o); p.stderr = stderr
    return p


def test_callresult_ok_carries_stop_reason():
    p = _outer('{"topics": ["x"]}')
    with patch("extractor.subprocess.run", return_value=p):
        cr = _extract_one_chunk("PROMPT", "content")
    assert isinstance(cr, CallResult)
    assert cr.signal["extraction_status"] == "ok"
    assert cr.stop_reason == "end_turn"
    assert cr.error is None


def test_callresult_nonzero_exit_parses_stdout_error():
    p = _outer("context length exceeded", is_error=True, api_err=400, rc=1, stderr="")
    with patch("extractor.subprocess.run", return_value=p):
        cr = _extract_one_chunk("PROMPT", "content")
    assert cr.signal["extraction_status"] == "failed"
    assert cr.api_error_status == 400
    assert "context length exceeded" in cr.error


def test_callresult_malformed_captures_raw():
    p = _outer("I cannot do that")
    with patch("extractor.subprocess.run", return_value=p):
        cr = _extract_one_chunk("PROMPT", "content")
    assert cr.signal["extraction_status"] == "malformed"
    assert cr.raw_response == "I cannot do that"


def test_callresult_nonzero_exit_unparseable_stdout():
    p = MagicMock(); p.returncode = 1; p.stdout = "this is not json"; p.stderr = "boom from cli"
    with patch("extractor.subprocess.run", return_value=p):
        cr = _extract_one_chunk("PROMPT", "content")
    assert cr.signal["extraction_status"] == "failed"
    assert "boom from cli" in cr.error


def test_callresult_subprocess_exception():
    import subprocess as _sp
    with patch("extractor.subprocess.run", side_effect=_sp.TimeoutExpired(cmd="claude", timeout=600)):
        cr = _extract_one_chunk("PROMPT", "content")
    assert cr.signal["extraction_status"] == "failed"
    assert cr.error  # non-empty


def test_lineage_records_failure_evidence():
    p = _outer("server exploded", is_error=True, api_err=500, rc=1, stderr="boom")
    with patch("extractor.subprocess.run", return_value=p):
        res = extract_session(FIXTURES / "tutoring_session.jsonl")
    lin = res.lineage
    assert lin["extraction_status"] == "failed"
    assert lin["error"] and lin["api_error_status"] == 500
    assert "stop_reason" in lin and "attempts" in lin
    assert "error" not in res.signal


def test_append_records_skips_signal_for_non_ok(tmp_path):
    from extractor import append_records
    append_records({"session_id": "s1", "extraction_status": "failed"},
                   {"session_id": "s1", "extraction_status": "failed"}, tmp_path)
    sj = tmp_path / "signal.jsonl"
    assert (not sj.exists()) or sj.read_text() == ""
    assert (tmp_path / "signal.meta.jsonl").read_text().strip()


def test_append_records_writes_signal_for_ok(tmp_path):
    from extractor import append_records
    append_records({"session_id": "s2", "extraction_status": "ok", "topics": ["t"]},
                   {"session_id": "s2", "extraction_status": "ok"}, tmp_path)
    assert (tmp_path / "signal.jsonl").read_text().strip()
    assert (tmp_path / "signal.meta.jsonl").read_text().strip()


def test_chunked_lineage_records_chunk_statuses(tmp_path, monkeypatch):
    # Force the chunked path on a tiny 2-line transcript: each line its own chunk.
    tx = tmp_path / "sess-chunked.jsonl"
    tx.write_text('{"role":"user","content":"line one here"}\n'
                  '{"role":"assistant","content":"line two here"}\n')
    monkeypatch.setattr("extractor.SINGLE_SHOT_MAX_BYTES", 1)
    monkeypatch.setattr("extractor.CHUNK_BYTES", 1)
    seq = [_outer("boom", is_error=True, rc=1),          # chunk 1 fails
           _outer('{"topics": ["t"]}')]                  # chunk 2 ok
    calls = {"n": 0}
    def fake_run(*a, **k):
        r = seq[calls["n"]]; calls["n"] += 1; return r
    monkeypatch.setattr("extractor.subprocess.run", fake_run)
    res = extract_session(tx)
    assert res.lineage["chunk_statuses"] == ["failed", "ok"]
    assert res.lineage["chunk_errors"]            # non-empty
    assert res.lineage["chunked"] is True
    assert res.signal["extraction_status"] == "ok" and res.signal.get("partial") is True


def test_chunked_all_fail_no_signal_error_and_meta_has_evidence(tmp_path, monkeypatch):
    tx = tmp_path / "sess-allfail.jsonl"
    tx.write_text('{"role":"user","content":"line one here"}\n'
                  '{"role":"assistant","content":"line two here"}\n')
    monkeypatch.setattr("extractor.SINGLE_SHOT_MAX_BYTES", 1)
    monkeypatch.setattr("extractor.CHUNK_BYTES", 1)
    seq = [_outer("boom one", is_error=True, rc=1),
           _outer("boom two", is_error=True, rc=1)]
    calls = {"n": 0}
    def fake_run(*a, **k):
        r = seq[calls["n"]]; calls["n"] += 1; return r
    monkeypatch.setattr("extractor.subprocess.run", fake_run)
    res = extract_session(tx)
    assert res.signal["extraction_status"] == "failed"
    assert "error" not in res.signal                       # Fix A contract
    assert res.lineage["chunk_statuses"] == ["failed", "failed"]
    assert res.lineage["chunk_errors"]                      # non-empty
    assert "all 2 chunks failed" in res.lineage["error"]
    assert res.lineage["chunked"] is True

    from extractor import append_records
    append_records(res.signal, res.lineage, tmp_path)
    sj = tmp_path / "signal.jsonl"
    assert (not sj.exists()) or sj.read_text() == ""
    assert (tmp_path / "signal.meta.jsonl").read_text().strip()


def test_lineage_raw_response_truncated(tmp_path):
    p = _outer("X" * 3000)   # non-JSON → malformed; raw_response captured
    with patch("extractor.subprocess.run", return_value=p):
        res = extract_session(FIXTURES / "tutoring_session.jsonl")
    assert res.lineage["extraction_status"] == "malformed"
    assert len(res.lineage["raw_response"]) == 2000
