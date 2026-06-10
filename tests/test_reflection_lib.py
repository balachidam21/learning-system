import json
from pathlib import Path

from lib.reflection import (
    normalize_title,
    proposal_id,
    load_ledger,
    open_titles,
    stale_accepted,
    weeks_ago,
)


def _write(ledger: Path, *rows):
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_normalize_title_lowercases_and_collapses_whitespace():
    assert normalize_title("  Create   a  Tutoring-Artifact  Skill ") == "create a tutoring-artifact skill"


def test_proposal_id_is_stable_and_type_sensitive():
    a = proposal_id("new_skill", "Create a tutoring-artifact skill")
    b = proposal_id("new_skill", "create   a tutoring-artifact   skill")  # same after normalize
    c = proposal_id("improve_skill", "Create a tutoring-artifact skill")  # different type
    assert a == b
    assert a != c
    assert a.startswith("sha256:")


def test_load_ledger_latest_wins_by_id(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        {"id": "x1", "type": "new_skill", "title": "T", "evidence": ["e1", "e2"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "x1", "status": "dismissed", "decided_week": "2026-W24", "handoff": None},
    )
    rows = load_ledger(ledger)
    assert rows["x1"]["status"] == "dismissed"
    # proposal fields survive the merge
    assert rows["x1"]["type"] == "new_skill"
    assert rows["x1"]["title"] == "T"
    assert rows["x1"]["created_week"] == "2026-W24"
    assert rows["x1"]["decided_week"] == "2026-W24"


def test_load_ledger_skips_corrupted_rows(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    ledger.write_text(
        '{"id": "ok", "type": "new_skill", "title": "T", "evidence": ["a","b"], '
        '"status": "pending", "created_week": "2026-W24"}\n'
        'this is not json\n'
        '\n'
        '[1, 2, 3]\n'  # valid JSON but not a dict -> skipped (no AttributeError)
        '{"id": "ok", "status": "accepted", "decided_week": "2026-W25", "handoff": null}\n'
    )
    rows = load_ledger(ledger)
    assert set(rows.keys()) == {"ok"}  # the non-dict array row is skipped, not crashed on
    assert rows["ok"]["status"] == "accepted"


def test_load_ledger_missing_file_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "nope.jsonl") == {}


def test_open_titles_returns_pending_and_dismissed_only(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        {"id": "p", "type": "new_skill", "title": "Pending one", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "d", "type": "workflow_fix", "title": "Dismissed one", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "d", "status": "dismissed", "decided_week": "2026-W24", "handoff": None},
        {"id": "a", "type": "new_check", "title": "Accepted one", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "a", "status": "accepted", "decided_week": "2026-W24", "handoff": None},
    )
    titles = open_titles(load_ledger(ledger))
    assert "Pending one" in titles
    assert "Dismissed one" in titles
    assert "Accepted one" not in titles  # accepted is neither open nor re-proposable


def test_weeks_ago_counts_iso_weeks():
    assert weeks_ago("2026-W24", "2026-W24") == 0
    assert weeks_ago("2026-W24", "2026-W25") == 1
    assert weeks_ago("2026-W24", "2026-W26") == 2


def test_stale_accepted_resurfaces_unbuilt_a_week_after_acceptance(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        # accepted last week, no handoff -> stale this week
        {"id": "stale", "type": "new_skill", "title": "Unbuilt", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W23"},
        {"id": "stale", "status": "accepted", "decided_week": "2026-W23", "handoff": None},
        # accepted last week WITH handoff -> not stale
        {"id": "done", "type": "new_skill", "title": "Built", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W23"},
        {"id": "done", "status": "accepted", "decided_week": "2026-W23",
         "handoff": "plan/specs/x.html"},
        # dismissed -> never stale
        {"id": "dis", "type": "workflow_fix", "title": "Nope", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W23"},
        {"id": "dis", "status": "dismissed", "decided_week": "2026-W23", "handoff": None},
        # accepted THIS week, no handoff -> not yet stale (age 0)
        {"id": "fresh", "type": "new_skill", "title": "Fresh", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "fresh", "status": "accepted", "decided_week": "2026-W24", "handoff": None},
    )
    rows = load_ledger(ledger)
    stale = stale_accepted(rows, ref_week="2026-W24")
    titles = [r["title"] for r in stale]
    assert titles == ["Unbuilt"]


def test_stale_accepted_measures_from_decided_week_not_created_week(tmp_path):
    """Staleness is a week since ACCEPTANCE, not since the proposal was created.
    A proposal created long ago but only just accepted is NOT immediately stale."""
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        # created W22, but only accepted at W24, no handoff
        {"id": "late", "type": "new_skill", "title": "Late-accepted", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W22"},
        {"id": "late", "status": "accepted", "decided_week": "2026-W24", "handoff": None},
    )
    rows = load_ledger(ledger)
    # checked at W24 (same week as acceptance) -> age 0 -> NOT stale,
    # even though created_week (W22) is two weeks old.
    assert stale_accepted(rows, ref_week="2026-W24") == []
    # checked at W25 (a week after acceptance) -> stale.
    assert [r["title"] for r in stale_accepted(rows, ref_week="2026-W25")] == ["Late-accepted"]


def test_load_ledger_orphan_transition_row_is_skipped(tmp_path):
    """A transition row arriving before/without its proposal row must not seed a
    bogus record (and must not corrupt a later proposal row's status)."""
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        {"id": "x1", "status": "accepted", "decided_week": "2026-W23", "handoff": None},  # orphan first
        {"id": "x1", "type": "new_skill", "title": "T", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W24"},
        {"id": "x1", "status": "dismissed", "decided_week": "2026-W24", "handoff": None},
    )
    rows = load_ledger(ledger)
    # the orphan transition did not poison the merge; the real history holds
    assert rows["x1"]["status"] == "dismissed"
    assert rows["x1"]["title"] == "T"


def test_stale_accepted_skips_malformed_decided_week(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    _write(
        ledger,
        {"id": "bad", "type": "new_skill", "title": "Bad week", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W23"},
        {"id": "bad", "status": "accepted", "decided_week": "not-a-week", "handoff": None},
        {"id": "ok", "type": "new_skill", "title": "Good week", "evidence": ["a", "b"],
         "status": "pending", "created_week": "2026-W23"},
        {"id": "ok", "status": "accepted", "decided_week": "2026-W23", "handoff": None},
    )
    stale = stale_accepted(load_ledger(ledger), ref_week="2026-W24")
    assert [r["title"] for r in stale] == ["Good week"]  # bad row skipped, not crashed on
