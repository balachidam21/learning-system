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
