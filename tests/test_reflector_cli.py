import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from reflector import _call_reflector, _validate_and_cap, ProposalSet

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_cli(result_text: str, returncode: int = 0):
    """Build a fake `claude -p --output-format=json` proc."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": result_text,
        "duration_ms": 100, "usage": {"input_tokens": 500, "output_tokens": 80},
        "total_cost_usd": 0.002, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    return proc


def _payload(proposals, cut=None):
    return json.dumps({"proposals": proposals, "cut": cut or []})


def test_call_reflector_parses_proposals():
    proposals = [
        {"type": "new_skill", "title": "Scaffold tutoring HTML",
         "evidence": ["built 2 html artifacts by hand", "flagged diagram bugs"]},
    ]
    with patch("reflector.subprocess.run", return_value=_mock_cli(f"```json\n{_payload(proposals)}\n```")):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert isinstance(ps, ProposalSet)
    assert ps.error is None
    assert ps.proposals[0]["title"] == "Scaffold tutoring HTML"


def test_call_reflector_handles_nonzero_exit():
    bad = MagicMock()
    bad.returncode = 1
    bad.stdout = ""
    bad.stderr = "simulated CLI failure"
    with patch("reflector.subprocess.run", return_value=bad):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert "simulated CLI failure" in ps.error


def test_call_reflector_handles_malformed_output():
    """CLI succeeds but the model returns prose, not JSON. Logged, no crash."""
    with patch("reflector.subprocess.run", return_value=_mock_cli("I could not find anything to propose.")):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert ps.error is not None
    assert ps.raw_response  # diagnostic trail kept


def test_validate_and_cap_enforces_min_evidence():
    raw = [
        {"type": "new_skill", "title": "good", "evidence": ["a", "b"]},
        {"type": "new_skill", "title": "thin", "evidence": ["only one"]},  # dropped
    ]
    kept = _validate_and_cap(raw)
    assert [p["title"] for p in kept] == ["good"]


def test_validate_and_cap_drops_unknown_types():
    raw = [{"type": "make_coffee", "title": "x", "evidence": ["a", "b"]}]
    assert _validate_and_cap(raw) == []


def test_validate_and_cap_caps_at_three():
    raw = [{"type": "new_skill", "title": f"t{i}", "evidence": ["a", "b"]} for i in range(5)]
    kept = _validate_and_cap(raw)
    assert len(kept) == 3
    assert [p["title"] for p in kept] == ["t0", "t1", "t2"]


def test_call_reflector_top_level_array_is_an_error_not_silent_empty():
    """A bare JSON array response must surface as an error with the raw trail,
    not silently mis-slice into one element / empty proposals."""
    arr = json.dumps([{"type": "new_skill", "title": "x", "evidence": ["a", "b"]}])
    with patch("reflector.subprocess.run", return_value=_mock_cli(arr)):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert ps.error is not None
    assert ps.raw_response


def test_call_reflector_dict_proposals_field_is_an_error():
    with patch("reflector.subprocess.run", return_value=_mock_cli(json.dumps({"proposals": {"k": "v"}, "cut": []}))):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert "expected list" in ps.error


def test_call_reflector_missing_result_field_is_explicit_error():
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "duration_ms": 1, "usage": {}, "total_cost_usd": 0.0,
                              "session_id": "x", "uuid": "y"})  # no "result" key
    proc.stderr = ""
    with patch("reflector.subprocess.run", return_value=proc):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert "missing 'result'" in ps.error


def test_validate_and_cap_requires_distinct_evidence():
    raw = [{"type": "new_skill", "title": "dup", "evidence": ["same", "same"]}]  # 2 items, 1 distinct
    assert _validate_and_cap(raw) == []


def test_call_reflector_sends_prompt_via_append_system_prompt_and_content_via_stdin():
    captured = {}
    def _capture(*args, **kwargs):
        captured["cmd"] = args[0]
        captured["stdin"] = kwargs.get("input")
        return _mock_cli(_payload([]))
    with patch("reflector.subprocess.run", side_effect=_capture):
        _call_reflector("THE PROMPT TEXT", "THE CONTENT")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--append-system-prompt") + 1] == "THE PROMPT TEXT"
    assert captured["stdin"] == "THE CONTENT"


def test_call_reflector_timeout_is_captured_as_error():
    import subprocess as _sp
    with patch("reflector.subprocess.run", side_effect=_sp.TimeoutExpired(cmd="claude", timeout=600)):
        ps = _call_reflector("PROMPT", "CONTENT")
    assert ps.proposals == []
    assert ps.error  # captured, not raised
