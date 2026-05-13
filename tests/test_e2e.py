"""End-to-end integration: fixture transcript → extractor → aggregator → HTML report."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from extractor import extract_session, append_records
from aggregator import build_report

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _mock_response(payload: dict):
    r = MagicMock()
    r.content = [MagicMock(text=json.dumps(payload))]
    r.usage = MagicMock(input_tokens=500, output_tokens=80)
    return r


def test_e2e_fixture_to_report(tmp_path):
    """Run the full pipeline on the tutoring fixture and assert the report
    contains the fixture's topic. Catches the empty-signal/bootstrap path."""
    expected = json.loads((FIXTURES / "expected/tutoring_session.json").read_text())

    project_dir = tmp_path
    log_dir = project_dir / "log"
    log_dir.mkdir(parents=True)
    (log_dir / "DAILY_LOG.md").write_text("")
    (log_dir / "PATCH_LIST.md").write_text("")

    # Stage 1: extract
    with patch("extractor._anthropic_client") as client:
        client.messages.create.return_value = _mock_response(expected)
        result = extract_session(FIXTURES / "tutoring_session.jsonl")
        append_records(result.signal, result.lineage, log_dir)

    # Confirm append worked
    assert (log_dir / "signal.jsonl").exists()
    assert (log_dir / "signal.meta.jsonl").exists()

    # Stage 2: aggregate (fixture is dated 2026-05-10 → ISO week 19)
    md_path, html_path = build_report(project_dir, week="2026-W19")

    md_text = md_path.read_text()
    # The report should NOT be the bootstrapping message — signal.jsonl has a record
    assert "Bootstrapping" not in md_text
    # The report should mention the fixture's topic somewhere
    assert "KL asymmetry" in md_text or "KL divergence" in md_text
    # HTML should be the real rendered output, not the placeholder
    html_text = html_path.read_text()
    assert "<!DOCTYPE html>" in html_text
    assert "plotly" in html_text.lower()
