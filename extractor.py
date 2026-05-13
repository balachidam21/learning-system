"""Session transcript → structured signal record."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

from lib.slug import transcripts_for_project
from lib.state import load_state, save_state, needs_extraction

ROOT = Path(__file__).parent
PROMPT_PATH = ROOT / "prompts" / "extract.v1.txt"
PROMPT_ID = "extract.v1"
MODEL = "claude-haiku-4-5"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/balajichidambaram/.local/bin/claude")
CLI_TIMEOUT_SEC = 600
STATE_PATH = ROOT / "state.json"
PROJECTS_FILE = ROOT / "projects.txt"

# Strip ```json ... ``` fences the model often wraps around JSON output
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _version() -> str:
    return (ROOT / "VERSION").read_text().strip()


def _prompt_hash(prompt_text: str) -> str:
    return "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


def _strip_code_fence(s: str) -> str:
    m = _FENCE_RE.match(s)
    return m.group(1) if m else s


@dataclass
class ExtractorResult:
    signal: Dict[str, Any]
    lineage: Dict[str, Any]


def extract_session(transcript_path: Path) -> ExtractorResult:
    """Run the extractor on one transcript via the claude CLI.
    Returns signal + lineage records. Does NOT write to disk — caller appends."""
    prompt_text = PROMPT_PATH.read_text()
    transcript_text = transcript_path.read_text()
    version = _version()
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0

    try:
        proc = subprocess.run(
            [
                CLAUDE_BIN, "-p",
                "--output-format", "json",
                "--model", MODEL,
                "--append-system-prompt", prompt_text,
                "--disable-slash-commands",
            ],
            input=transcript_text,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SEC,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:500]}")

        outer = json.loads(proc.stdout)
        if outer.get("is_error"):
            raise RuntimeError(f"claude CLI reported error: {outer.get('subtype')}")

        result_text = outer.get("result", "")
        usage = outer.get("usage", {})
        tokens_in = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost_usd = outer.get("total_cost_usd", 0.0)
    except Exception as e:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "failed",
            "error": str(e)[:500],
        }
        lineage = _lineage(transcript_path, signal, version, prompt_text,
                           tokens_in, tokens_out, cost_usd)
        return ExtractorResult(signal=signal, lineage=lineage)

    # Stage 2: parse the model's response as JSON
    try:
        signal = json.loads(_strip_code_fence(result_text))
        signal.setdefault("extraction_status", "ok")
    except json.JSONDecodeError as e:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "malformed",
            "error": f"non-JSON response: {e}",
            "raw_response": result_text[:2000],
        }

    lineage = _lineage(transcript_path, signal, version, prompt_text,
                       tokens_in, tokens_out, cost_usd)
    return ExtractorResult(signal=signal, lineage=lineage)


def _lineage(transcript_path: Path, signal: Dict[str, Any], version: str,
             prompt_text: str, tokens_in: int, tokens_out: int,
             cost_usd: float) -> Dict[str, Any]:
    return {
        "session_id": signal.get("session_id", transcript_path.stem),
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extractor_version": version,
        "prompt_id": PROMPT_ID,
        "prompt_hash": _prompt_hash(prompt_text),
        "model": MODEL,
        "backend": "claude-cli",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd_reported": cost_usd,
        "extraction_status": signal["extraction_status"],
    }


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
    """Report planned extractions and estimated token counts. No CLI calls."""
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
    print(f"\nEstimated input tokens: ~{est_tokens:,}")
    print("Uses Claude Code subscription auth — no API key needed.")


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract learning signals from Claude Code session transcripts. "
                    "Use --scan-all for cron mode, or --transcript + --project-log-dir for one-off.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report planned extractions without calling the CLI")
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
