import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import reflector
from lib.reflection import load_ledger, proposal_id

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_cli(proposals):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": json.dumps({"proposals": proposals, "cut": []}),
        "duration_ms": 100, "usage": {"input_tokens": 5, "output_tokens": 5},
        "total_cost_usd": 0.0, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    return proc


def test_main_reflect_then_decide(tmp_path, capsys):
    project = tmp_path
    (project / "log").mkdir()
    (project / "log" / "signal.jsonl").write_text(
        (FIXTURES / "reflection_signal.jsonl").read_text())

    proposals = [{"type": "new_skill", "title": "Embed revision panels in artifacts",
                  "evidence": ["flags own forgetting tendency", "wants revision panels embedded"]}]

    # Stage 1: reflect via argv
    argv = ["reflector.py", "reflect", "--project-dir", str(project), "--week", "2026-W24"]
    with patch("reflector.subprocess.run", return_value=_mock_cli(proposals)), \
         patch.object(sys, "argv", argv):
        reflector._main()
    out = json.loads(capsys.readouterr().out)
    assert len(out["new_pending"]) == 1
    rid = out["new_pending"][0]["id"]
    # Pin the full stdout shape /bird-eye renders (type/title/evidence, not just id)
    p0 = out["new_pending"][0]
    assert p0["type"] == "new_skill"
    assert p0["title"] == "Embed revision panels in artifacts"
    assert p0["evidence"] == ["flags own forgetting tendency", "wants revision panels embedded"]

    # Stage 2: accept it via argv with a handoff
    argv2 = ["reflector.py", "decide", rid, "--project-dir", str(project),
             "--accept", "--handoff", "plan/specs/new-skill.html", "--week", "2026-W24"]
    with patch.object(sys, "argv", argv2):
        reflector._main()

    rows = load_ledger(project / "log" / "reflections" / "proposals.jsonl")
    assert rows[rid]["status"] == "accepted"
    assert rows[rid]["handoff"] == "plan/specs/new-skill.html"
    assert rid == proposal_id("new_skill", "Embed revision panels in artifacts")


def test_main_reflect_stdout_pins_stale_accepted_shape(tmp_path, capsys):
    """/bird-eye renders stale_accepted[].{title, decided_week} from stdout — pin them."""
    project = tmp_path
    (project / "log").mkdir()
    (project / "log" / "signal.jsonl").write_text(
        (FIXTURES / "reflection_signal.jsonl").read_text())
    ledger_dir = project / "log" / "reflections"
    ledger_dir.mkdir(parents=True)
    rid = proposal_id("new_skill", "Old accepted thing")
    (ledger_dir / "proposals.jsonl").write_text(
        json.dumps({"id": rid, "type": "new_skill", "title": "Old accepted thing",
                    "evidence": ["a a", "b b"], "status": "pending", "created_week": "2026-W23"}) + "\n"
        + json.dumps({"id": rid, "status": "accepted", "decided_week": "2026-W23", "handoff": None}) + "\n"
    )
    argv = ["reflector.py", "reflect", "--project-dir", str(project), "--week", "2026-W24"]
    with patch("reflector.subprocess.run", return_value=_mock_cli([])), \
         patch.object(sys, "argv", argv):
        reflector._main()
    out = json.loads(capsys.readouterr().out)
    assert out["new_pending"] == []
    s0 = out["stale_accepted"][0]
    assert s0["title"] == "Old accepted thing"
    assert s0["decided_week"] == "2026-W23"
    assert s0["id"] == rid
