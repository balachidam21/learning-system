import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import reflector
from reflector import reflect, ReflectionResult
from lib.reflection import load_ledger, proposal_id

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_cli(proposals, cut=None):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": json.dumps({"proposals": proposals, "cut": cut or []}),
        "duration_ms": 100, "usage": {"input_tokens": 500, "output_tokens": 80},
        "total_cost_usd": 0.002, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    return proc


def _seed_project(tmp_path):
    """Minimal opted-in project: signal window + empty artifact bundle dirs."""
    log = tmp_path / "log"
    (log).mkdir()
    (log / "signal.jsonl").write_text((FIXTURES / "reflection_signal.jsonl").read_text())
    return tmp_path


def test_reflect_appends_pending_rows(tmp_path):
    project = _seed_project(tmp_path)
    proposals = [{"type": "new_skill", "title": "Scaffold tutoring HTML",
                  "evidence": ["built 2 html artifacts by hand", "flagged diagram bugs twice"]}]
    with patch("reflector.subprocess.run", return_value=_mock_cli(proposals)):
        result = reflect(project, week="2026-W24")
    assert isinstance(result, ReflectionResult)
    assert len(result.new_pending) == 1
    ledger = load_ledger(project / "log" / "reflections" / "proposals.jsonl")
    rid = proposal_id("new_skill", "Scaffold tutoring HTML")
    assert ledger[rid]["status"] == "pending"
    assert ledger[rid]["created_week"] == "2026-W24"
    assert ledger[rid]["evidence"] == ["built 2 html artifacts by hand", "flagged diagram bugs twice"]


def test_reflect_exact_id_replay_not_re_added(tmp_path):
    """Re-running the same week with the same proposal does not duplicate the row."""
    project = _seed_project(tmp_path)
    proposals = [{"type": "new_skill", "title": "Scaffold tutoring HTML",
                  "evidence": ["a a a", "b b b"]}]
    with patch("reflector.subprocess.run", return_value=_mock_cli(proposals)):
        reflect(project, week="2026-W24")
        result2 = reflect(project, week="2026-W24")
    assert result2.new_pending == []  # hash backstop suppressed the replay
    ledger_lines = (project / "log" / "reflections" / "proposals.jsonl").read_text().strip().splitlines()
    assert len(ledger_lines) == 1  # only the first append


def test_reflect_injects_open_titles_into_prompt_for_paraphrase_dedup(tmp_path):
    """Semantic dedup is enforced via the prompt: a previously-dismissed title
    must appear in the --append-system-prompt text sent to the CLI so the model
    won't re-propose a paraphrase. (The hash CANNOT catch the paraphrase; the
    prompt is the guard.)"""
    project = _seed_project(tmp_path)
    # Pre-seed the ledger: a dismissed proposal.
    ledger_dir = project / "log" / "reflections"
    ledger_dir.mkdir(parents=True)
    rid = proposal_id("new_skill", "Create a tutoring-artifact skill")
    (ledger_dir / "proposals.jsonl").write_text(
        json.dumps({"id": rid, "type": "new_skill", "title": "Create a tutoring-artifact skill",
                    "evidence": ["a a", "b b"], "status": "pending", "created_week": "2026-W23"}) + "\n"
        + json.dumps({"id": rid, "status": "dismissed", "decided_week": "2026-W23", "handoff": None}) + "\n"
    )
    # The stubbed CLI returns a PARAPHRASE of the dismissed proposal.
    paraphrase = [{"type": "new_skill", "title": "Add a skill that scaffolds HTML+SVG tutoring pages",
                   "evidence": ["built html artifacts by hand", "flagged diagram bugs"]}]
    captured = {}

    def _capture(*args, **kwargs):
        captured["cmd"] = args[0]
        return _mock_cli(paraphrase)

    with patch("reflector.subprocess.run", side_effect=_capture):
        reflect(project, week="2026-W24")

    # Assert the dismissed title was injected into the prompt text passed via the
    # --append-system-prompt CLI argument (the open titles flow through the prompt
    # template, NOT through stdin).
    prompt_arg = captured["cmd"][captured["cmd"].index("--append-system-prompt") + 1]
    assert "Create a tutoring-artifact skill" in prompt_arg
    # Document: semantic suppression of the paraphrase is the model's job given that
    # injected title; the hash backstop alone cannot catch it (different id).
    assert proposal_id("new_skill", paraphrase[0]["title"]) != rid


def test_reflect_empty_path_renders_sane_result(tmp_path):
    """No proposals -> empty new_pending, no ledger file required, no crash."""
    project = _seed_project(tmp_path)
    with patch("reflector.subprocess.run", return_value=_mock_cli([])):
        result = reflect(project, week="2026-W24")
    assert result.new_pending == []
    assert result.stale_accepted == []
    assert result.error is None


def test_reflect_malformed_cli_output_logged_no_crash(tmp_path):
    project = _seed_project(tmp_path)
    bad = MagicMock()
    bad.returncode = 0
    bad.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "sorry, no JSON here", "duration_ms": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0.0,
        "session_id": "x", "uuid": "y",
    })
    bad.stderr = ""
    with patch("reflector.subprocess.run", return_value=bad):
        result = reflect(project, week="2026-W24")
    assert result.new_pending == []
    assert result.error is not None  # surfaced, not swallowed


def test_reflect_degrades_to_signal_only_when_artifacts_missing(tmp_path):
    """If the artifact bundle files don't exist, reflect still runs on signal alone."""
    project = _seed_project(tmp_path)  # no PATCH_LIST.md / weekly/ / CURRENT_STATE.md
    proposals = [{"type": "workflow_fix", "title": "Operationalize the perishable track",
                  "evidence": ["0 reps two weeks running", "skipped Saturday"]}]
    with patch("reflector.subprocess.run", return_value=_mock_cli(proposals)):
        result = reflect(project, week="2026-W24")
    assert len(result.new_pending) == 1  # degraded, not crashed


def test_reflect_follow_through_lists_stale_accepted(tmp_path):
    """An accepted+handoff-null row whose acceptance (decided_week) is older than a
    week re-surfaces in the result."""
    project = _seed_project(tmp_path)
    ledger_dir = project / "log" / "reflections"
    ledger_dir.mkdir(parents=True)
    rid = proposal_id("new_skill", "Old accepted thing")
    (ledger_dir / "proposals.jsonl").write_text(
        json.dumps({"id": rid, "type": "new_skill", "title": "Old accepted thing",
                    "evidence": ["a a", "b b"], "status": "pending", "created_week": "2026-W23"}) + "\n"
        + json.dumps({"id": rid, "status": "accepted", "decided_week": "2026-W23", "handoff": None}) + "\n"
    )
    with patch("reflector.subprocess.run", return_value=_mock_cli([])):
        result = reflect(project, week="2026-W24")
    assert [r["title"] for r in result.stale_accepted] == ["Old accepted thing"]
