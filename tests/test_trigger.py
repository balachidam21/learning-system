from pathlib import Path

import trigger


def _write_projects(tmp_path, *paths):
    p = tmp_path / "projects.txt"
    p.write_text("\n".join(paths) + "\n")
    return p


def test_opted_in_returns_true(tmp_path):
    pj = _write_projects(tmp_path, "/Users/x/code/ai-inference-track")
    assert trigger.should_trigger("/Users/x/code/ai-inference-track", pj) is True


def test_trailing_slash_normalized(tmp_path):
    pj = _write_projects(tmp_path, "/Users/x/code/ai-inference-track/")
    assert trigger.should_trigger("/Users/x/code/ai-inference-track", pj) is True


def test_not_opted_in_returns_false(tmp_path):
    pj = _write_projects(tmp_path, "/Users/x/code/ai-inference-track")
    assert trigger.should_trigger("/Users/x/other", pj) is False


def test_comments_and_blanks_ignored(tmp_path):
    pj = tmp_path / "projects.txt"
    pj.write_text("# a comment\n\n/Users/x/code/ai-inference-track\n")
    assert trigger.should_trigger("/Users/x/code/ai-inference-track", pj) is True


def test_missing_projects_txt_returns_false(tmp_path):
    assert trigger.should_trigger("/Users/x/code/ai-inference-track", tmp_path / "nope.txt") is False


def test_empty_project_dir_returns_false(tmp_path):
    pj = _write_projects(tmp_path, "/Users/x/code/ai-inference-track")
    assert trigger.should_trigger("", pj) is False


def test_main_returns_0_and_skips_launchctl_when_not_opted_in(monkeypatch):
    monkeypatch.setattr(trigger, "should_trigger", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(trigger.subprocess, "run", lambda *a, **k: calls.append(a))
    assert trigger.main() == 0
    assert calls == []


def test_main_runs_launchctl_when_opted_in(monkeypatch):
    monkeypatch.setattr(trigger, "should_trigger", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(trigger.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    assert trigger.main() == 0
    assert len(calls) == 1
    assert calls[0][0] == "launchctl" and calls[0][1] == "start"
    assert calls[0][2].endswith(".learning-system.extractor")


def test_main_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(trigger, "should_trigger", boom)
    assert trigger.main() == 0  # exception swallowed, hook never fails
