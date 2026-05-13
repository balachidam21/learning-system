from pathlib import Path
from aggregator import build_report

FIXTURE_SIGNAL = Path(__file__).parent / "fixtures" / "synthetic_signal.jsonl"


def test_build_report_html_contains_charts(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "signal.jsonl").write_text(FIXTURE_SIGNAL.read_text())
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    (project_dir / "log" / "PATCH_LIST.md").write_text("")

    _, html_path = build_report(project_dir, week="2026-W19")
    html = html_path.read_text()

    assert "<!DOCTYPE html>" in html
    assert "plotly" in html.lower()
    assert "Hours per week" in html
    assert "Time since last touch" in html
