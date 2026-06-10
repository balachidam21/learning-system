"""Weekly dream-reflection pass: distilled signal + artifacts -> typed proposals.

Mirrors extractor.py's claude-CLI invocation (MODEL pin, CLAUDE_BIN override,
CLI_TIMEOUT_SEC, robust JSON parse). Ledger I/O and the `decide` subcommand are
added in later tasks. Runs only inside /bird-eye (user awake -> CLI works).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib.reflection import (
    current_week,
    load_ledger,
    normalize_title,
    open_titles,
    proposal_id,
    stale_accepted,
)

ROOT = Path(__file__).parent
PROMPT_PATH = ROOT / "prompts" / "reflection.v1.txt"
PROMPT_ID = "reflection.v1"
MODEL = "claude-opus-4-7"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/balajichidambaram/.local/bin/claude")
CLI_TIMEOUT_SEC = 600

MAX_PROPOSALS_PER_WEEK = 3
MIN_EVIDENCE = 2
VALID_TYPES = ("new_skill", "improve_skill", "workflow_fix", "new_check")

SIGNAL_WINDOW_WEEKS = 3
ARTIFACT_MAX_BYTES = 20_000  # per artifact file, head-trimmed to bound prompt size

_FENCE_SEARCH_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _robust_json_parse(text: str) -> Optional[Dict[str, Any]]:
    """Layered: bare -> fenced-anywhere -> outermost {...}. Returns dict or None.
    Same heuristic as extractor._robust_json_parse.

    Known limitation: a non-JSON '{' appearing before the real object defeats
    the first-'{'/last-'}' slice heuristic.
    """
    if not text or not text.strip():
        return None
    s = text.strip()
    # A bare top-level JSON array is not a proposal envelope; surfacing None here
    # lets the caller report an error instead of the first-'{'/last-'}' slice
    # silently extracting a single element and "succeeding".
    try:
        bare = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        bare = None
    if isinstance(bare, list):
        return None
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
class ProposalSet:
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    cut: List[str] = field(default_factory=list)
    error: Optional[str] = None
    raw_response: Optional[str] = None


@dataclass
class ReflectionResult:
    new_pending: List[Dict[str, Any]] = field(default_factory=list)
    stale_accepted: List[Dict[str, Any]] = field(default_factory=list)
    cut: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _call_reflector(prompt_text: str, content: str) -> ProposalSet:
    """Single claude CLI invocation -> ProposalSet. Never raises; failures and
    malformed output are captured on the ProposalSet (mirror the extractor)."""
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
        return ProposalSet(error=str(e)[:500])

    outer = None
    if proc.stdout:
        try:
            outer = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            outer = None
    is_dict = isinstance(outer, dict)

    if proc.returncode != 0:
        detail = str(outer.get("result") or outer.get("subtype") or "") if is_dict else ""
        return ProposalSet(error=f"claude CLI exit {proc.returncode}: {(detail or proc.stderr)[:500]}")
    if not is_dict:
        return ProposalSet(error=f"unparseable stdout: {(proc.stderr or proc.stdout)[:400]}")
    if outer.get("is_error"):
        detail = str(outer.get("result") or outer.get("subtype") or "")
        return ProposalSet(error=f"claude CLI reported error: {detail[:300]}")

    result_text = outer.get("result")
    if result_text is None:
        return ProposalSet(error="outer JSON missing 'result' field")
    parsed = _robust_json_parse(result_text)
    if parsed is None:
        return ProposalSet(error="non-JSON response", raw_response=result_text[:2000])

    raw_proposals = parsed.get("proposals", []) or []
    if not isinstance(raw_proposals, list):
        return ProposalSet(
            error=f"proposals field is {type(raw_proposals).__name__}, expected list",
            raw_response=result_text[:2000],
        )
    raw_cut = parsed.get("cut", []) or []
    if not isinstance(raw_cut, list):
        raw_cut = []  # cut is non-critical; coerce silently
    return ProposalSet(proposals=raw_proposals, cut=raw_cut)


def _validate_and_cap(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep well-formed proposals (known type + title + >= MIN_EVIDENCE distinct
    citations), cap at MAX_PROPOSALS_PER_WEEK, preserving the model's order."""
    kept: List[Dict[str, Any]] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        if p.get("type") not in VALID_TYPES:
            continue
        title = p.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        evidence = [e for e in (p.get("evidence") or []) if isinstance(e, str) and e.strip()]
        if len(set(evidence)) < MIN_EVIDENCE:
            continue
        kept.append({"type": p["type"], "title": title.strip(), "evidence": evidence})
        if len(kept) >= MAX_PROPOSALS_PER_WEEK:
            break
    return kept


def _load_signal_window(signal_path: Path, week: str,
                        window_weeks: int = SIGNAL_WINDOW_WEEKS) -> List[Dict[str, Any]]:
    """Latest-wins-by-session-id load (like aggregator._load_signals), filtered to
    the trailing `window_weeks` ISO weeks ending at `week` (inclusive)."""
    if not signal_path.exists():
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    for line in signal_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        sid = rec.get("session_id")
        if sid is None:
            continue
        if sid not in seen or rec.get("ended_at", "") >= seen[sid].get("ended_at", ""):
            seen[sid] = rec
    year, w = week.split("-W")
    end = datetime.date.fromisocalendar(int(year), int(w), 1) + datetime.timedelta(days=7)
    start = end - datetime.timedelta(weeks=window_weeks)
    out = []
    for rec in seen.values():
        try:
            t = datetime.datetime.fromisoformat(
                (rec.get("started_at", "") or "").replace("Z", "")).date()
        except ValueError:
            continue
        if start <= t < end:
            out.append(rec)
    return out


def _artifact_bundle(project_dir: Path) -> str:
    """Best-effort read of the system's own artifacts. Missing files are skipped
    (degrade to signal-only). Each file is head-trimmed to ARTIFACT_MAX_BYTES."""
    parts: List[str] = []

    skills_dir = project_dir / ".claude" / "skills"
    if skills_dir.exists():
        names = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
        if names:
            parts.append("EXISTING SKILLS: " + ", ".join(names))

    for label, rel in (("PATCH_LIST", "log/PATCH_LIST.md"),
                       ("CURRENT_STATE", "plan/CURRENT_STATE.md")):
        p = project_dir / rel
        if p.exists():
            parts.append(f"--- {label} ---\n{p.read_text()[:ARTIFACT_MAX_BYTES]}")

    weekly_dir = project_dir / "log" / "weekly"
    if weekly_dir.exists():
        recents = sorted(weekly_dir.glob("*.md"))[-2:]
        for p in recents:
            parts.append(f"--- WEEKLY {p.name} ---\n{p.read_text()[:ARTIFACT_MAX_BYTES]}")

    return "\n\n".join(parts)


def _build_content(signals: List[Dict[str, Any]], artifacts: str) -> str:
    """Assemble the user-message content. Open titles do NOT flow here — they fill
    the prompt's {open_titles} slot via PROMPT_PATH formatting in reflect()."""
    return (
        "SIGNAL (trailing window):\n"
        + "\n".join(json.dumps(s) for s in signals)
        + "\n\nARTIFACTS:\n" + (artifacts or "(none available)")
    )


def _append_ledger_rows(ledger_path: Path, rows: List[Dict[str, Any]]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def reflect(project_dir: Path, week: Optional[str] = None) -> ReflectionResult:
    """Run the weekly reflection pass for one project. Appends new pending rows
    to the ledger and returns new_pending + stale_accepted for /bird-eye."""
    week = week or current_week()
    signal_path = project_dir / "log" / "signal.jsonl"
    ledger_path = project_dir / "log" / "reflections" / "proposals.jsonl"

    rows = load_ledger(ledger_path)
    titles = open_titles(rows)
    stale = stale_accepted(rows, ref_week=week)

    signals = _load_signal_window(signal_path, week)
    if not signals:
        # nothing happened in the window — don't burn a CLI call on zero evidence
        return ReflectionResult(stale_accepted=stale)
    artifacts = _artifact_bundle(project_dir)

    prompt_text = PROMPT_PATH.read_text().replace(
        "{open_titles}",
        "\n".join(f"- {t}" for t in titles) if titles else "(none yet)",
    )
    content = _build_content(signals, artifacts)

    ps = _call_reflector(prompt_text, content)
    if ps.error:
        return ReflectionResult(stale_accepted=stale, error=ps.error)

    kept = _validate_and_cap(ps.proposals)

    # Hash backstop: drop exact-replay duplicates already present in the ledger,
    # AND collapse same-id duplicates emitted within this single batch (the model
    # can paraphrase to the same normalized title twice; _validate_and_cap does
    # not dedup, so without `seen` we would append two rows with one id).
    new_rows: List[Dict[str, Any]] = []
    seen: set = set()
    for p in kept:
        rid = proposal_id(p["type"], p["title"])
        if rid in rows or rid in seen:
            continue
        seen.add(rid)
        new_rows.append({
            "id": rid, "type": p["type"], "title": p["title"],
            "evidence": p["evidence"], "status": "pending", "created_week": week,
        })
    if new_rows:
        _append_ledger_rows(ledger_path, new_rows)

    return ReflectionResult(new_pending=new_rows, stale_accepted=stale, cut=ps.cut)


def decide(project_dir: Path, proposal_id_str: str, accept: bool,
           handoff: Optional[str] = None, week: Optional[str] = None) -> Dict[str, Any]:
    """Append an accept/dismiss transition row for an existing proposal id.

    Append-only: the original row is never rewritten; load_ledger resolves the
    final state latest-wins. Raises KeyError if the id isn't in the ledger.
    """
    week = week or current_week()
    ledger_path = project_dir / "log" / "reflections" / "proposals.jsonl"
    rows = load_ledger(ledger_path)
    if proposal_id_str not in rows:
        raise KeyError(f"unknown proposal id: {proposal_id_str}")
    transition = {
        "id": proposal_id_str,
        "status": "accepted" if accept else "dismissed",
        "decided_week": week,
        "handoff": handoff if accept else None,
    }
    _append_ledger_rows(ledger_path, [transition])
    return transition


def _main() -> None:
    parser = argparse.ArgumentParser(description="Dream-reflection weekly pass + decisions.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reflect = sub.add_parser("reflect", help="Run the weekly reflection pass.")
    p_reflect.add_argument("--project-dir", type=Path, required=True)
    p_reflect.add_argument("--week", type=str, default=None, help="ISO week like 2026-W24")

    p_decide = sub.add_parser("decide", help="Accept or dismiss a proposal by id.")
    p_decide.add_argument("id")
    p_decide.add_argument("--project-dir", type=Path, required=True)
    g = p_decide.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--dismiss", action="store_true")
    p_decide.add_argument("--handoff", type=str, default=None)
    p_decide.add_argument("--week", type=str, default=None)

    args = parser.parse_args()

    if args.cmd == "reflect":
        result = reflect(args.project_dir, week=args.week)
        print(json.dumps({
            "new_pending": result.new_pending,
            "stale_accepted": result.stale_accepted,
            "cut": result.cut,
            "error": result.error,
        }, indent=2))
        return
    if args.cmd == "decide":
        t = decide(args.project_dir, args.id, accept=args.accept,
                   handoff=args.handoff, week=args.week)
        print(json.dumps(t))
        return


if __name__ == "__main__":
    _main()
