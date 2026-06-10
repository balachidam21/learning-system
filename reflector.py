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

_FENCE_SEARCH_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _robust_json_parse(text: str) -> Optional[Dict[str, Any]]:
    """Layered: bare -> fenced-anywhere -> outermost {...}. Returns dict or None.
    Same heuristic as extractor._robust_json_parse."""
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
class ProposalSet:
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    cut: List[str] = field(default_factory=list)
    error: Optional[str] = None
    raw_response: Optional[str] = None


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

    result_text = outer.get("result", "") or ""
    parsed = _robust_json_parse(result_text)
    if parsed is None:
        return ProposalSet(error="non-JSON response", raw_response=result_text[:2000])
    return ProposalSet(
        proposals=parsed.get("proposals", []) or [],
        cut=parsed.get("cut", []) or [],
    )


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


def _main() -> None:
    parser = argparse.ArgumentParser(description="Dream-reflection weekly pass.")
    parser.parse_args()
    parser.error("subcommands wired up in a later task")


if __name__ == "__main__":
    _main()
