import json
from pathlib import Path
from drift_monitor import build_drift_report


def test_coverage_gap_detected(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    # 2 manual entries but only 1 auto record
    (project_dir / "log" / "DAILY_LOG.md").write_text(
        "## Log Entries\n\n### May 10, 2026\n\n### May 12, 2026\n"
    )
    signal = {"session_id": "s1", "started_at": "2026-05-10T19:00:00Z",
              "topics": ["X"], "patch_list_deltas_inferred": [], "extraction_status": "ok"}
    (project_dir / "log" / "signal.jsonl").write_text(json.dumps(signal) + "\n")

    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    assert "Coverage" in text
    assert "2 manual" in text or "missed" in text.lower()


def test_failure_rate_reported(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    (project_dir / "log" / "signal.jsonl").write_text(
        json.dumps({"session_id": "a", "started_at": "2026-05-01T19:00:00Z",
                    "extraction_status": "failed"}) + "\n"
        + json.dumps({"session_id": "b", "started_at": "2026-05-02T19:00:00Z",
                      "extraction_status": "ok"}) + "\n"
    )
    out = build_drift_report(project_dir, month="2026-05")
    assert "Failure rate" in out.read_text()
    assert "50" in out.read_text() or "1/2" in out.read_text()


def test_invalid_date_header_does_not_crash(tmp_path):
    """A malformed date in DAILY_LOG.md should be silently skipped, not crash."""
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    # "May 32, 2026" is invalid — should not crash, just get skipped
    (project_dir / "log" / "DAILY_LOG.md").write_text(
        "## Log Entries\n\n### May 10, 2026\n\n### May 32, 2026\n"
    )
    (project_dir / "log" / "signal.jsonl").write_text("")
    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    # Only the valid May 10 should be counted
    assert "1 manual log entries" in text


def test_failed_records_without_started_at_are_counted(tmp_path):
    """Failed extractions don't have started_at; failure rate must still include them."""
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    # Production-shape failed record (no started_at)
    failed_rec = {"session_id": "abc", "extraction_status": "failed", "error": "timeout"}
    ok_rec = {"session_id": "def", "started_at": "2026-05-15T19:00:00Z",
              "extraction_status": "ok"}
    (project_dir / "log" / "signal.jsonl").write_text(
        json.dumps(failed_rec) + "\n" + json.dumps(ok_rec) + "\n"
    )
    # Lineage for both — gives the failed one a timestamp
    failed_meta = {"session_id": "abc", "extracted_at": "2026-05-10T10:00:00Z",
                   "extraction_status": "failed"}
    ok_meta = {"session_id": "def", "extracted_at": "2026-05-15T19:30:00Z",
               "extraction_status": "ok"}
    (project_dir / "log" / "signal.meta.jsonl").write_text(
        json.dumps(failed_meta) + "\n" + json.dumps(ok_meta) + "\n"
    )

    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    # Failed record should be counted: 1/2 = 50%
    assert "1/2" in text
    assert "50.0%" in text
