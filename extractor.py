"""Session transcript → structured signal record."""
import argparse
import hashlib
import json
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

from lib.slug import transcripts_for_project
from lib.state import load_state, save_state, needs_extraction

try:
    from anthropic import Anthropic
    _anthropic_client = Anthropic()
except Exception:
    _anthropic_client = None  # tests will mock

ROOT = Path(__file__).parent
PROMPT_PATH = ROOT / "prompts" / "extract.v1.txt"
PROMPT_ID = "extract.v1"
MODEL = "claude-opus-4-7"
STATE_PATH = ROOT / "state.json"
PROJECTS_FILE = ROOT / "projects.txt"


def _version() -> str:
    return (ROOT / "VERSION").read_text().strip()


def _prompt_hash(prompt_text: str) -> str:
    return "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


@dataclass
class ExtractorResult:
    signal: Dict[str, Any]
    lineage: Dict[str, Any]


def extract_session(transcript_path: Path) -> ExtractorResult:
    """Run the extractor on one transcript. Returns signal + lineage records.
    Does NOT write to disk — caller appends."""
    if _anthropic_client is None:
        raise RuntimeError(
            "Anthropic client not initialized — ensure ANTHROPIC_API_KEY is set "
            "and 'anthropic' is installed."
        )

    prompt_text = PROMPT_PATH.read_text()
    transcript_text = transcript_path.read_text()
    version = _version()

    try:
        response = _anthropic_client.messages.create(
            model=MODEL,
            max_tokens=2000,
            temperature=0.1,
            system=[{"type": "text", "text": prompt_text,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": transcript_text}],
        )
        raw_text = response.content[0].text
        tokens_in = getattr(response.usage, "input_tokens", 0)
        tokens_out = getattr(response.usage, "output_tokens", 0)
    except Exception as e:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "failed",
            "error": str(e),
        }
        tokens_in = 0
        tokens_out = 0
    else:
        try:
            signal = json.loads(raw_text)
            signal.setdefault("extraction_status", "ok")
        except json.JSONDecodeError as e:
            signal = {
                "session_id": transcript_path.stem,
                "extraction_status": "malformed",
                "error": f"non-JSON response: {e}",
                "raw_response": raw_text[:2000],
            }

    lineage = {
        "session_id": signal.get("session_id", transcript_path.stem),
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extractor_version": version,
        "prompt_id": PROMPT_ID,
        "prompt_hash": _prompt_hash(prompt_text),
        "model": MODEL,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "extraction_status": signal["extraction_status"],
    }
    return ExtractorResult(signal=signal, lineage=lineage)


def append_records(signal: Dict[str, Any], lineage: Dict[str, Any], log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "signal.jsonl").open("a") as f:
        f.write(json.dumps(signal) + "\n")
    with (log_dir / "signal.meta.jsonl").open("a") as f:
        f.write(json.dumps(lineage) + "\n")


def _read_projects(projects_file: Path) -> list:
    if not projects_file.exists():
        return []
    return [Path(line.strip()) for line in projects_file.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def scan_all(claude_root: Path = Path.home() / ".claude",
             projects_file: Path = PROJECTS_FILE,
             state_path: Path = STATE_PATH) -> int:
    """Scan opted-in projects for new/changed transcripts, extract each.
    Returns count of sessions extracted this run."""
    state = load_state(state_path)
    current_version = _version()
    extracted = 0
    for project_path in _read_projects(projects_file):
        log_dir = project_path / "log"
        if not log_dir.exists():
            continue
        for transcript in transcripts_for_project(project_path, claude_root=claude_root):
            if not needs_extraction(transcript, state, current_version):
                continue
            result = extract_session(transcript)
            append_records(result.signal, result.lineage, log_dir)
            extracted += 1
            # Only checkpoint successful extractions; failed/malformed will retry next run
            if result.lineage["extraction_status"] == "ok":
                mtime = datetime.datetime.fromtimestamp(
                    transcript.stat().st_mtime, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                state["sessions"][transcript.stem] = {
                    "transcript_path": str(transcript),
                    "last_mtime": mtime,
                    "last_extracted_at": result.lineage["extracted_at"],
                    "extraction_status": result.lineage["extraction_status"],
                }
                save_state(state_path, state)
    state["extractor_version"] = current_version
    save_state(state_path, state)
    return extracted


def _dry_run_scan(claude_root: Path = Path.home() / ".claude",
                  projects_file: Path = PROJECTS_FILE,
                  state_path: Path = STATE_PATH) -> None:
    """Report planned extractions and estimated token counts. No API calls."""
    state = load_state(state_path)
    current_version = _version()
    total_chars = 0
    sessions_to_extract = []
    for project_path in _read_projects(projects_file):
        log_dir = project_path / "log"
        if not log_dir.exists():
            continue
        for transcript in transcripts_for_project(project_path, claude_root=claude_root):
            if not needs_extraction(transcript, state, current_version):
                continue
            size = transcript.stat().st_size
            sessions_to_extract.append((project_path, transcript, size))
            total_chars += size
    print(f"Would extract {len(sessions_to_extract)} session(s):")
    for project_path, transcript, size in sessions_to_extract:
        print(f"  {transcript.name} ({size:,} bytes) → {project_path}/log/")
    # rough token estimate: ~4 chars/token
    est_tokens = total_chars // 4
    est_cost_opus = est_tokens / 1_000_000 * 15.0  # $15/M input tokens for Opus
    print(f"\nEstimated input tokens: ~{est_tokens:,}")
    print(f"Estimated cost (Opus, input only): ~${est_cost_opus:.2f}")
    print("This is an estimate — actual cost depends on output tokens too.")


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract learning signals from Claude Code session transcripts. "
                    "Use --scan-all for cron mode, or --transcript + --project-log-dir for one-off.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report planned extractions without calling the API")
    parser.add_argument("--scan-all", action="store_true")
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--project-log-dir", type=Path)
    args = parser.parse_args()

    if args.dry_run:
        _dry_run_scan()
        return

    if args.scan_all:
        n = scan_all()
        print(f"extracted {n} session(s)")
        return
    if args.transcript and args.project_log_dir:
        result = extract_session(args.transcript)
        append_records(result.signal, result.lineage, args.project_log_dir)
        print(f"extracted 1 session from {args.transcript}")
        return
    parser.error("must pass --scan-all OR --transcript + --project-log-dir")


if __name__ == "__main__":
    _main()
