from pathlib import Path
from aggregator import build_report

FIXTURE_SIGNAL = Path(__file__).parent / "fixtures" / "synthetic_signal.jsonl"


def test_build_report_markdown_contains_required_sections(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "signal.jsonl").write_text(FIXTURE_SIGNAL.read_text())
    (project_dir / "log" / "DAILY_LOG.md").write_text("# Daily Log\n\n## Log Entries\n")
    (project_dir / "log" / "PATCH_LIST.md").write_text("# Patch List\n")

    md_path, _ = build_report(project_dir, week="2026-W19")
    md = md_path.read_text()

    assert "## Pace" in md
    assert "## What's been solid longest" in md
    assert "## Trajectory" in md
    assert "## Patterns" in md
    assert "aggregator v" in md.lower()  # lineage footer


def test_build_report_handles_empty_signal(tmp_path):
    project_dir = tmp_path
    (project_dir / "log").mkdir()
    (project_dir / "log" / "signal.jsonl").write_text("")
    (project_dir / "log" / "DAILY_LOG.md").write_text("")
    (project_dir / "log" / "PATCH_LIST.md").write_text("")

    md_path, _ = build_report(project_dir, week="2026-W19")
    assert "bootstrapping" in md_path.read_text().lower()
