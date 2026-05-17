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
    """Failed extractions don't have started_at; failure rate must still include them.
    Under D2, failure rate is meta-sourced. signal.jsonl has only the ok record."""
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    # signal.jsonl: ok record only (failed never lands here post-Task-3)
    ok_rec = {"session_id": "def", "started_at": "2026-05-15T19:00:00Z",
              "extraction_status": "ok"}
    (project_dir / "log" / "signal.jsonl").write_text(json.dumps(ok_rec) + "\n")
    # meta ledger: both records, failed has extracted_at for month-filtering
    failed_meta = {"session_id": "abc", "extracted_at": "2026-05-10T10:00:00Z",
                   "extraction_status": "failed"}
    ok_meta = {"session_id": "def", "extracted_at": "2026-05-15T19:30:00Z",
               "extraction_status": "ok"}
    (project_dir / "log" / "signal.meta.jsonl").write_text(
        json.dumps(failed_meta) + "\n" + json.dumps(ok_meta) + "\n"
    )

    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    # Meta-sourced failure count: 1 failed / 2 attempts = 50%
    assert "1/2" in text
    assert "50.0%" in text


def test_drift_failure_rate_from_meta(tmp_path):
    """Guard: failure rate is sourced from meta, not signal.jsonl.
    signal.jsonl has only 1 ok record but meta has 1 ok + 1 failed.
    Report must show 1/2 = 50%, proving it reads meta."""
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    # signal.jsonl: only the ok record (no failures — as in production)
    (project_dir / "log" / "signal.jsonl").write_text(
        json.dumps({"session_id": "s1", "started_at": "2026-05-10T19:00:00Z",
                    "extraction_status": "ok"}) + "\n"
    )
    # meta: 1 ok + 1 failed
    (project_dir / "log" / "signal.meta.jsonl").write_text(
        json.dumps({"session_id": "s1", "extracted_at": "2026-05-10T19:05:00Z",
                    "extraction_status": "ok"}) + "\n"
        + json.dumps({"session_id": "s2", "extracted_at": "2026-05-11T09:00:00Z",
                      "extraction_status": "failed", "error": "boom"}) + "\n"
    )
    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    # If failure rate were sourced from signal.jsonl it would be 0/1=0%, not 1/2=50%
    assert "1/2" in text
    assert "50.0%" in text


def test_skipped_too_large_excluded_from_failure_rate(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    (project_dir / "log" / "signal.jsonl").write_text(
        json.dumps({"session_id": "ok1", "started_at": "2026-05-03T19:00:00Z",
                    "extraction_status": "ok"}) + "\n")
    (project_dir / "log" / "signal.meta.jsonl").write_text(
        json.dumps({"session_id": "ok1", "extracted_at": "2026-05-03T19:30:00Z",
                    "extraction_status": "ok"}) + "\n"
        + json.dumps({"session_id": "f1", "extracted_at": "2026-05-04T02:00:00Z",
                      "extraction_status": "failed", "error": "boom"}) + "\n"
        + json.dumps({"session_id": "big", "extracted_at": "2026-05-05T02:00:00Z",
                      "extraction_status": "skipped_too_large"}) + "\n")
    out = build_drift_report(project_dir, month="2026-05")
    text = out.read_text()
    # skipped excluded from BOTH numerator and denominator → 1 failed / 2 considered
    assert "1/2" in text and "50.0%" in text
    assert "1 transcript(s) skipped" in text
    # the misleading 'review error.log' recommendation must NOT fire at 50%>10%? It SHOULD
    # fire (genuine 50% failure) — but verify it's driven by the 50%, not by the skip:
    assert "Failure rate above" in text
