import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from extractor import extract_session, ExtractorResult

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _mock_anthropic_response(payload: dict):
    """Build a fake Anthropic Messages API response."""
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(payload))]
    resp.usage = MagicMock(input_tokens=1200, output_tokens=180)
    return resp


def test_extract_tutoring_session():
    expected = json.loads((FIXTURES / "expected/tutoring_session.json").read_text())
    transcript = FIXTURES / "tutoring_session.jsonl"

    with patch("extractor._anthropic_client") as client:
        client.messages.create.return_value = _mock_anthropic_response(expected)
        result = extract_session(transcript)

    assert isinstance(result, ExtractorResult)
    assert result.signal["session_id"] == "f4a8-tut-001"
    assert "KL asymmetry" in result.signal["topics"]
    assert result.lineage["extractor_version"]
    assert result.lineage["prompt_id"] == "extract.v1"
    assert result.lineage["tokens_in"] == 1200


def test_extract_logging_session():
    expected = json.loads((FIXTURES / "expected/logging_session.json").read_text())
    transcript = FIXTURES / "logging_session.jsonl"

    with patch("extractor._anthropic_client") as client:
        client.messages.create.return_value = _mock_anthropic_response(expected)
        result = extract_session(transcript)

    assert result.signal["topics"] == ["cross-entropy as likelihood"]
    assert result.signal["patch_list_deltas_inferred"][0]["to"] == "🟢"


def test_extract_handles_api_failure():
    transcript = FIXTURES / "tutoring_session.jsonl"
    with patch("extractor._anthropic_client") as client:
        client.messages.create.side_effect = RuntimeError("simulated timeout")
        result = extract_session(transcript)
    assert result.signal["extraction_status"] == "failed"


def test_extract_handles_non_json_response():
    transcript = FIXTURES / "tutoring_session.jsonl"
    resp = MagicMock()
    resp.content = [MagicMock(text="not actually json, sorry")]
    resp.usage = MagicMock(input_tokens=100, output_tokens=10)
    with patch("extractor._anthropic_client") as client:
        client.messages.create.return_value = resp
        result = extract_session(transcript)
    assert result.signal["extraction_status"] == "malformed"
    assert "raw_response" in result.signal
