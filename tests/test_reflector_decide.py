import json
from pathlib import Path

from reflector import decide
from lib.reflection import load_ledger, proposal_id


def _seed_pending(tmp_path, title="A proposal"):
    ledger = tmp_path / "log" / "reflections" / "proposals.jsonl"
    ledger.parent.mkdir(parents=True)
    rid = proposal_id("new_skill", title)
    ledger.write_text(json.dumps({
        "id": rid, "type": "new_skill", "title": title, "evidence": ["a a", "b b"],
        "status": "pending", "created_week": "2026-W24"}) + "\n")
    return tmp_path, rid, ledger


def test_decide_accept_appends_transition_row(tmp_path):
    project, rid, ledger = _seed_pending(tmp_path)
    decide(project, rid, accept=True, handoff="plan/specs/x.html", week="2026-W24")
    rows = load_ledger(ledger)
    assert rows[rid]["status"] == "accepted"
    assert rows[rid]["decided_week"] == "2026-W24"
    assert rows[rid]["handoff"] == "plan/specs/x.html"
    # original proposal fields preserved (event-sourced merge)
    assert rows[rid]["title"] == "A proposal"
    # exactly one extra line was appended (append-only, never rewritten)
    assert len(ledger.read_text().strip().splitlines()) == 2


def test_decide_dismiss_appends_transition_row(tmp_path):
    project, rid, ledger = _seed_pending(tmp_path)
    decide(project, rid, accept=False, week="2026-W24")
    rows = load_ledger(ledger)
    assert rows[rid]["status"] == "dismissed"
    assert rows[rid]["handoff"] is None


def test_decide_accept_without_handoff_sets_null(tmp_path):
    project, rid, ledger = _seed_pending(tmp_path)
    decide(project, rid, accept=True, week="2026-W24")
    rows = load_ledger(ledger)
    assert rows[rid]["status"] == "accepted"
    assert rows[rid]["handoff"] is None  # the follow-through check will re-surface it


def test_decide_unknown_id_raises(tmp_path):
    project, rid, ledger = _seed_pending(tmp_path)
    import pytest
    with pytest.raises(KeyError):
        decide(project, "sha256:doesnotexist", accept=True, week="2026-W24")
