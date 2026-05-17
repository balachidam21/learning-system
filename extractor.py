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
from typing import Dict, Any, Optional, Tuple

from lib.slug import transcripts_for_project
from lib.state import load_state, save_state, needs_extraction

ROOT = Path(__file__).parent
PROMPT_PATH = ROOT / "prompts" / "extract.v1.txt"
PROMPT_ID = "extract.v1"
MODEL = "claude-opus-4-7"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/balajichidambaram/.local/bin/claude")
SINGLE_SHOT_MAX_BYTES = 700_000   # empirically safe under subscription per-request cap
CHUNK_BYTES = 600_000              # per-chunk size when splitting; leaves headroom
MAX_CHUNKS = 8                     # absolute ceiling — refuse transcripts requiring >8 chunks
# Absolute size beyond which we skip even with chunking.
MAX_TRANSCRIPT_BYTES = MAX_CHUNKS * CHUNK_BYTES  # 4_800_000
CLI_TIMEOUT_SEC = 600
STATE_PATH = ROOT / "state.json"
PROJECTS_FILE = ROOT / "projects.txt"

# Strip ```json ... ``` fences the model often wraps around JSON output
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
# Same fence body as _FENCE_RE but unanchored — finds a fenced block amid prose
_FENCE_SEARCH_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _version() -> str:
    return (ROOT / "VERSION").read_text().strip()


def _prompt_hash(prompt_text: str) -> str:
    return "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


def _strip_code_fence(s: str) -> str:
    m = _FENCE_RE.match(s)
    return m.group(1) if m else s


def _robust_json_parse(text: str) -> Optional[Dict[str, Any]]:
    """Layered: bare → fenced-anywhere → outermost {...}. Returns dict or None.

    Known limitation: a non-JSON '{' appearing before the real object defeats
    the first-'{'/last-'}' slice heuristic.
    """
    if not text or not text.strip():
        return None
    s = text.strip()
    candidates = [s]
    fence = _FENCE_SEARCH_RE.search(s)
    if fence:
        candidates.append(fence.group(1).strip())
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i and s[i:j + 1] != s:
        candidates.append(s[i:j + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


@dataclass
class CallResult:
    signal: Dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    stop_reason: Optional[str] = None
    api_error_status: Optional[Any] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None
    attempts: int = 1


@dataclass
class ExtractorResult:
    signal: Dict[str, Any]
    lineage: Dict[str, Any]


def _chunk_transcript(text: str, chunk_bytes: int = CHUNK_BYTES) -> list[str]:
    """Split transcript into chunks of <= chunk_bytes, breaking on line boundaries.
    Each chunk is a complete set of JSONL records — never a partial line."""
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        line_size = len(line.encode("utf-8"))
        if current_size + line_size > chunk_bytes and current:
            chunks.append("".join(current))
            current = [line]
            current_size = line_size
        else:
            current.append(line)
            current_size += line_size
    if current:
        chunks.append("".join(current))
    return chunks


def _merge_signals(signals: list[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
    """Combine N per-chunk signals into one record for the whole session.
    Union sets for topics/skills/artifacts/preferences; concat for markers/deltas;
    earliest started_at / latest ended_at; sum duration_min."""
    def uniq(items):
        seen = set()
        out = []
        for x in items:
            key = json.dumps(x, sort_keys=True) if isinstance(x, dict) else x
            if key not in seen:
                seen.add(key)
                out.append(x)
        return out

    merged: Dict[str, Any] = {
        "session_id": session_id,
        "topics": uniq([t for s in signals for t in s.get("topics", []) or []]),
        "skills_used": uniq([k for s in signals for k in s.get("skills_used", []) or []]),
        "tutoring_artifacts": uniq([a for s in signals for a in s.get("tutoring_artifacts", []) or []]),
        "struggle_markers": [m for s in signals for m in s.get("struggle_markers", []) or []],
        "click_markers": [m for s in signals for m in s.get("click_markers", []) or []],
        "patch_list_deltas_inferred": uniq([d for s in signals for d in s.get("patch_list_deltas_inferred", []) or []]),
        "user_preference_hints": uniq([h for s in signals for h in s.get("user_preference_hints", []) or []]),
        "extraction_status": "ok",
    }
    # started_at = earliest; ended_at = latest
    starts = [s.get("started_at") for s in signals if s.get("started_at")]
    ends = [s.get("ended_at") for s in signals if s.get("ended_at")]
    if starts:
        merged["started_at"] = min(starts)
    if ends:
        merged["ended_at"] = max(ends)
    # duration: sum if individual durations exist, else compute from start/end if both present
    durations = [s.get("duration_min", 0) for s in signals if s.get("duration_min")]
    if durations:
        merged["duration_min"] = sum(durations)
    merged["chunked"] = True
    merged["chunk_count"] = len(signals)
    return merged


def _extract_one_chunk(prompt_text: str, content: str) -> "CallResult":
    """Run a single claude CLI invocation. Returns a CallResult.
    Signal dict has extraction_status set to 'ok', 'failed', or 'malformed'."""
    try:
        proc = subprocess.run(
            [
                CLAUDE_BIN, "-p",
                "--output-format", "json",
                "--model", MODEL,
                "--append-system-prompt", prompt_text,
                "--disable-slash-commands",
            ],
            input=content,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SEC,
            check=False,
        )
    except Exception as e:
        return CallResult(signal={"extraction_status": "failed"}, error=str(e)[:500])

    outer = None
    if proc.stdout:
        try:
            outer = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            outer = None
    is_dict = isinstance(outer, dict)
    stop_reason = outer.get("stop_reason") if is_dict else None
    api_error_status = outer.get("api_error_status") if is_dict else None

    if proc.returncode != 0:
        detail = str(outer.get("result") or outer.get("subtype") or "") if is_dict else ""
        return CallResult(signal={"extraction_status": "failed"},
                          error=f"claude CLI exit {proc.returncode}: {(detail or proc.stderr)[:500]}",
                          stop_reason=stop_reason, api_error_status=api_error_status)
    if not is_dict:
        return CallResult(signal={"extraction_status": "failed"},
                          error=f"unparseable CLI stdout: {proc.stdout[:300]}",
                          stop_reason=stop_reason, api_error_status=api_error_status)
    if outer.get("is_error"):
        detail = str(outer.get("result") or outer.get("subtype") or "")
        return CallResult(signal={"extraction_status": "failed"},
                          error=f"claude CLI reported error: {detail[:300]}",
                          stop_reason=stop_reason, api_error_status=api_error_status)

    result_text = outer.get("result", "") or ""
    usage = outer.get("usage", {}) or {}
    tokens_in = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                 + usage.get("cache_creation_input_tokens", 0))
    tokens_out = usage.get("output_tokens", 0)
    cost_usd = outer.get("total_cost_usd", 0.0)

    parsed = _robust_json_parse(result_text)
    if parsed is None:
        # raw_response (≤2000 chars) is the diagnostic trail for malformed output
        return CallResult(signal={"extraction_status": "malformed"},
                          tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
                          stop_reason=stop_reason, api_error_status=api_error_status,
                          error="non-JSON response", raw_response=result_text[:2000])
    parsed.setdefault("extraction_status", "ok")
    return CallResult(signal=parsed, tokens_in=tokens_in, tokens_out=tokens_out,
                      cost_usd=cost_usd, stop_reason=stop_reason,
                      api_error_status=api_error_status)


def extract_session(transcript_path: Path) -> ExtractorResult:
    """Run the extractor on one transcript via the claude CLI.
    Returns signal + lineage records. Does NOT write to disk — caller appends."""
    prompt_text = PROMPT_PATH.read_text()
    version = _version()

    size_bytes = transcript_path.stat().st_size

    if size_bytes > MAX_TRANSCRIPT_BYTES:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "skipped_too_large",
            "error": f"transcript {size_bytes} bytes exceeds MAX_TRANSCRIPT_BYTES={MAX_TRANSCRIPT_BYTES} (would need >{MAX_CHUNKS} chunks)",
        }
        lineage = _lineage(transcript_path, signal, version, prompt_text, 0, 0, 0.0)
        return ExtractorResult(signal=signal, lineage=lineage)

    transcript_text = transcript_path.read_text()

    if size_bytes <= SINGLE_SHOT_MAX_BYTES:
        # Single-shot path
        cr = _extract_one_chunk(prompt_text, transcript_text)
        signal = cr.signal
        if cr.error and "error" not in signal:
            signal["error"] = cr.error
        if cr.raw_response and "raw_response" not in signal:
            signal["raw_response"] = cr.raw_response
        signal.setdefault("session_id", transcript_path.stem)
        lineage = _lineage(transcript_path, signal, version, prompt_text,
                           cr.tokens_in, cr.tokens_out, cr.cost_usd)
        return ExtractorResult(signal=signal, lineage=lineage)

    # Chunked path
    chunks = _chunk_transcript(transcript_text)
    if len(chunks) > MAX_CHUNKS:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "skipped_too_large",
            "error": f"would need {len(chunks)} chunks, max is {MAX_CHUNKS}",
        }
        lineage = _lineage(transcript_path, signal, version, prompt_text, 0, 0, 0.0)
        return ExtractorResult(signal=signal, lineage=lineage)

    chunk_results = []
    total_tokens_in = total_tokens_out = 0
    total_cost = 0.0
    any_failed = False
    for i, chunk in enumerate(chunks):
        # Hint to the model which chunk this is (helps it not over-claim duration etc.)
        prefixed = f"[Chunk {i+1} of {len(chunks)} from session]\n{chunk}"
        cr = _extract_one_chunk(prompt_text, prefixed)
        total_tokens_in += cr.tokens_in
        total_tokens_out += cr.tokens_out
        total_cost += cr.cost_usd
        if cr.signal.get("extraction_status") in ("failed", "malformed"):
            any_failed = True
        chunk_results.append(cr)
    chunk_signals = [cr.signal for cr in chunk_results]

    # Filter out failed chunks before merging — keep only the ones that produced real data
    ok_signals = [s for s in chunk_signals if s.get("extraction_status") == "ok"]
    if not ok_signals:
        signal = {
            "session_id": transcript_path.stem,
            "extraction_status": "failed",
            "error": f"all {len(chunks)} chunks failed extraction",
        }
    else:
        signal = _merge_signals(ok_signals, transcript_path.stem)
        if any_failed:
            signal["partial"] = True
            signal["chunk_failures"] = sum(1 for s in chunk_signals if s.get("extraction_status") != "ok")

    lineage = _lineage(transcript_path, signal, version, prompt_text,
                       total_tokens_in, total_tokens_out, total_cost)
    lineage["chunked"] = True
    lineage["chunk_count"] = len(chunks)
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

    single_shot = [(p, t, s) for p, t, s in sessions_to_extract if s <= SINGLE_SHOT_MAX_BYTES]
    chunked = [(p, t, s) for p, t, s in sessions_to_extract if SINGLE_SHOT_MAX_BYTES < s <= MAX_TRANSCRIPT_BYTES]
    skipped = [(p, t, s) for p, t, s in sessions_to_extract if s > MAX_TRANSCRIPT_BYTES]

    if chunked:
        print(f"\n{len(chunked)} transcript(s) will be CHUNKED:")
        for p, t, s in chunked:
            est_chunks = (s // CHUNK_BYTES) + 1
            print(f"  {t.name} ({s:,} bytes) → ~{est_chunks} chunks")
    if skipped:
        print(f"\n⚠ {len(skipped)} transcript(s) too large even for chunking (>{MAX_TRANSCRIPT_BYTES:,} bytes):")
        for p, t, s in skipped:
            print(f"  {t.name} ({s:,} bytes) → SKIPPED")


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
