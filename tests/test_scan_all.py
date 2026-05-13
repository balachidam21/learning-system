import datetime
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from extractor import scan_all, STATE_PATH


def _mock_cli(payload):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": json.dumps(payload),
        "duration_ms": 100, "usage": {"input_tokens": 100, "output_tokens": 50},
        "total_cost_usd": 0.001, "session_id": "x", "uuid": "y",
    })
    proc.stderr = ""
    return proc


def test_scan_all_processes_each_opted_in_project(tmp_path, monkeypatch):
    # Fake claude root with one project slug + one transcript
    claude_root = tmp_path / "claude"
    project_dir = tmp_path / "myproj"
    log_dir = project_dir / "log"
    log_dir.mkdir(parents=True)
    slug = str(project_dir).replace("/", "-")
    (claude_root / "projects" / slug).mkdir(parents=True)
    transcript = claude_root / "projects" / slug / "sess1.jsonl"
    transcript.write_text('{"type":"meta","session_id":"sess1"}\n')

    # Opt-in registry
    projects_txt = tmp_path / "projects.txt"
    projects_txt.write_text(str(project_dir) + "\n")

    # State file in a temp location
    state_path = tmp_path / "state.json"

    fake_signal = {"session_id": "sess1", "topics": ["x"], "extraction_status": "ok"}

    with patch("extractor.subprocess.run", return_value=_mock_cli(fake_signal)):
        n = scan_all(
            claude_root=claude_root,
            projects_file=projects_txt,
            state_path=state_path,
        )

    assert n == 1
    assert (log_dir / "signal.jsonl").read_text().strip()
    assert state_path.exists()


def test_scan_all_skips_unchanged_sessions(tmp_path):
    claude_root = tmp_path / "claude"
    project_dir = tmp_path / "myproj"
    (project_dir / "log").mkdir(parents=True)
    slug = str(project_dir).replace("/", "-")
    (claude_root / "projects" / slug).mkdir(parents=True)
    transcript = claude_root / "projects" / slug / "sess1.jsonl"
    transcript.write_text("dummy")

    projects_txt = tmp_path / "projects.txt"
    projects_txt.write_text(str(project_dir))

    # Pre-seed state as if already extracted at current mtime
    mtime = datetime.datetime.fromtimestamp(
        transcript.stat().st_mtime, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "schema_version": 1, "extractor_version": "0.1.0",
        "sessions": {"sess1": {"last_mtime": mtime}}
    }))

    n = scan_all(claude_root=claude_root, projects_file=projects_txt, state_path=state_path)
    assert n == 0


def test_scan_all_retries_failed_extractions(tmp_path):
    """A failed extraction should not be checkpointed, so it retries on next run."""
    claude_root = tmp_path / "claude"
    project_dir = tmp_path / "myproj"
    (project_dir / "log").mkdir(parents=True)
    slug = str(project_dir).replace("/", "-")
    (claude_root / "projects" / slug).mkdir(parents=True)
    transcript = claude_root / "projects" / slug / "sess1.jsonl"
    transcript.write_text('{"type":"meta","session_id":"sess1"}\n')

    projects_txt = tmp_path / "projects.txt"
    projects_txt.write_text(str(project_dir))

    state_path = tmp_path / "state.json"

    # First run: CLI fails — extract_session returns a "failed" stub
    from extractor import scan_all
    bad = MagicMock()
    bad.returncode = 1
    bad.stdout = ""
    bad.stderr = "first call fails"
    with patch("extractor.subprocess.run", return_value=bad):
        n1 = scan_all(claude_root=claude_root, projects_file=projects_txt, state_path=state_path)

    assert n1 == 1  # we did attempt the extraction
    state = json.loads(state_path.read_text())
    # Failed extraction must NOT be checkpointed in state["sessions"]
    assert "sess1" not in state.get("sessions", {})

    # Second run: CLI succeeds — should retry the failed transcript
    fake_signal = {"session_id": "sess1", "topics": ["x"], "extraction_status": "ok"}
    with patch("extractor.subprocess.run", return_value=_mock_cli(fake_signal)):
        n2 = scan_all(claude_root=claude_root, projects_file=projects_txt, state_path=state_path)

    assert n2 == 1  # retried, not skipped
    state = json.loads(state_path.read_text())
    assert "sess1" in state["sessions"]
    assert state["sessions"]["sess1"]["extraction_status"] == "ok"
