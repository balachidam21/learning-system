from pathlib import Path
from typing import List

DEFAULT_CLAUDE_ROOT = Path.home() / ".claude"


def path_to_slug(project_path: Path) -> str:
    """Encode a filesystem path the way Claude Code does: replace '/' with '-'."""
    return str(project_path).replace("/", "-")


def transcripts_for_project(project_path: Path, claude_root: Path = DEFAULT_CLAUDE_ROOT) -> List[Path]:
    """Return all session JSONL transcript files for a given project, or [] if none."""
    slug = path_to_slug(project_path)
    transcripts_dir = claude_root / "projects" / slug
    if not transcripts_dir.exists():
        return []
    return sorted(transcripts_dir.glob("*.jsonl"))
