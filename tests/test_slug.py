from pathlib import Path
from lib.slug import path_to_slug, transcripts_for_project

def test_path_to_slug_basic():
    assert path_to_slug(Path("/Users/foo/code/proj")) == "-Users-foo-code-proj"

def test_path_to_slug_preserves_internal_dashes():
    assert path_to_slug(Path("/Users/foo/ai-inference-track")) == "-Users-foo-ai-inference-track"

def test_transcripts_for_project_returns_empty_when_dir_missing(tmp_path):
    fake_claude_root = tmp_path / "claude"
    project = Path("/nonexistent/project")
    assert transcripts_for_project(project, claude_root=fake_claude_root) == []
