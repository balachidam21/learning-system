#!/usr/bin/env python3
"""Idempotently (un)install the learning-system SessionStart hook in
~/.claude/settings.json. Before writing, backs up the current file to
settings.json.bak (this is the pre-THIS-run state; repeated runs roll it forward,
so it is not a permanent pre-install snapshot)."""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK_COMMAND = f"{ROOT}/.venv/bin/python {ROOT}/trigger.py"


def add_hook(settings: dict, command: str) -> dict:
    """Ensure the SessionStart command hook is present, mutating `settings` in place and returning it (idempotent)."""
    session_start = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    for group in session_start:
        for h in group.get("hooks", []):
            if h.get("command") == command:
                return settings  # already present
    session_start.append({"hooks": [{"type": "command", "command": command}]})
    return settings


def remove_hook(settings: dict, command: str) -> dict:
    """Remove the command hook from SessionStart, mutating `settings` in place and
    returning it. No-op (and does NOT create a 'hooks' key) if there are none."""
    if "hooks" not in settings or "SessionStart" not in settings["hooks"]:
        return settings
    session_start = settings["hooks"]["SessionStart"]
    for group in session_start:
        group["hooks"] = [h for h in group.get("hooks", []) if h.get("command") != command]
    settings["hooks"]["SessionStart"] = [g for g in session_start if g.get("hooks")]
    return settings


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        raise SystemExit(f"install_hook: {path} is not valid JSON; fix it before installing.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "uninstall"])
    ap.add_argument("--settings", type=Path, default=SETTINGS)
    ap.add_argument("--command", default=HOOK_COMMAND)
    args = ap.parse_args(argv)

    settings = _load(args.settings)
    if args.settings.exists():
        shutil.copy(args.settings, str(args.settings) + ".bak")
    args.settings.parent.mkdir(parents=True, exist_ok=True)

    settings = add_hook(settings, args.command) if args.action == "install" else remove_hook(settings, args.command)
    args.settings.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"install_hook: {args.action}ed SessionStart hook in {args.settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
