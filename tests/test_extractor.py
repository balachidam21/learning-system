import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from extractor import extract_session, ExtractorResult

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
