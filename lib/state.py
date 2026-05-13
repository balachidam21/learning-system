import json
import datetime
from pathlib import Path
from typing import Dict, Any

EMPTY_STATE: Dict[str, Any] = {
    "schema_version": 1,
    "extractor_version": None,
    "sessions": {},
}


def load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return dict(EMPTY_STATE, sessions={})
    return json.loads(state_path.read_text())


def save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))


def needs_extraction(transcript: Path, state: Dict[str, Any], current_version: str) -> bool:
    """A transcript needs (re)extraction if it has no prior record,
    or its mtime is newer than the recorded one, or the extractor version bumped."""
    if state.get("extractor_version") != current_version:
        return True
    session_id = transcript.stem
    prior = state.get("sessions", {}).get(session_id)
    if prior is None:
        return True
    current_mtime = datetime.datetime.utcfromtimestamp(transcript.stat().st_mtime).isoformat() + "Z"
    return current_mtime != prior.get("last_mtime")
