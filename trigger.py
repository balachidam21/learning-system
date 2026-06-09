#!/usr/bin/env python3
"""Event-driven trigger for the extractor — registered as a SessionStart hook.

Reads CLAUDE_PROJECT_DIR (fallback: cwd). If it is an opted-in project (listed in
projects.txt), runs `launchctl start` on the extractor LaunchAgent, which executes
detached in the user session (Keychain access, survives Claude Code exit). Returns
immediately — never blocks session start, never fails it.
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PROJECTS_TXT = ROOT / "projects.txt"


def _normalize(p: str) -> str:
    return os.path.normpath(os.path.expanduser(p.strip()))


def should_trigger(project_dir: str, projects_txt: Path = PROJECTS_TXT) -> bool:
    """True iff project_dir is one of the opted-in projects in projects.txt."""
    if not project_dir or not projects_txt.exists():
        return False
    target = _normalize(project_dir)
    for line in projects_txt.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _normalize(line) == target:
            return True
    return False


def main() -> int:
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        if not should_trigger(project_dir):
            return 0
        label = f"com.{getpass.getuser()}.learning-system.extractor"
        # `launchctl start` tells launchd to run the (loaded) agent detached and
        # returns immediately. Errors (e.g. agent not loaded yet) are swallowed.
        subprocess.run(
            ["launchctl", "start", label],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # A SessionStart hook must NEVER fail the session — swallow everything.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
