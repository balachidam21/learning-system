"""Pure helpers for dream-reflection: ledger load, dedup, follow-through.

No subprocess, no claude CLI — orchestration lives in reflector.py. These
helpers mirror the latest-wins-by-id pattern of aggregator._load_signals and
the ISO-week format of aggregator._current_week.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, collapse internal whitespace, strip. Stable for exact replay
    only — paraphrase dedup is handled by the LLM via the in-prompt ledger."""
    return _WS_RE.sub(" ", title.strip().lower())


def proposal_id(ptype: str, title: str) -> str:
    """Exact-replay backstop id: hash of type + normalized title."""
    key = f"{ptype}\n{normalize_title(title)}"
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def current_week(today: datetime.date = None) -> str:
    """ISO week like '2026-W24' — same format as aggregator._current_week."""
    today = today or datetime.date.today()
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


def weeks_ago(created_week: str, ref_week: str) -> int:
    """Whole ISO weeks between created_week and ref_week (ref - created).

    Anchors each '<year>-W<week>' to its ISO Monday and takes the day delta // 7,
    so it spans year boundaries correctly.
    """
    def monday(week: str) -> datetime.date:
        year, w = week.split("-W")
        return datetime.date.fromisocalendar(int(year), int(w), 1)
    return (monday(ref_week) - monday(created_week)).days // 7


def load_ledger(ledger_path: Path) -> Dict[str, Dict[str, Any]]:
    """Event-sourced load: latest-wins by id over append-only rows.

    Proposal rows seed the full record; later transition rows overlay
    status/decided_week/handoff by id. Corrupted/blank lines are skipped
    (mirror aggregator._load_signals resilience).
    """
    if not ledger_path.exists():
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    for line in ledger_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if not rid:
            continue
        if rid not in rows:
            rows[rid] = dict(rec)
        else:
            rows[rid].update(rec)
    return rows


def open_titles(rows: Dict[str, Dict[str, Any]]) -> List[str]:
    """Titles the reflector must NOT re-propose: pending + dismissed.

    Accepted ones are intentionally excluded — an accepted proposal is being
    acted on (or re-surfaced via stale_accepted), not re-proposed.
    """
    out = []
    for r in rows.values():
        if r.get("status") in ("pending", "dismissed") and r.get("title"):
            out.append(r["title"])
    return out


def stale_accepted(rows: Dict[str, Dict[str, Any]], ref_week: str,
                   stale_weeks: int = 1) -> List[Dict[str, Any]]:
    """Accepted rows with no handoff, older than `stale_weeks` ISO weeks since
    ACCEPTANCE (`decided_week`), not since the proposal was created.

    Deterministic — no LLM. Applies the un-operationalized-commitment lesson to
    the system's own output: accepted-but-unbuilt proposals re-surface a week
    after they were accepted.
    """
    out = []
    for r in rows.values():
        if r.get("status") != "accepted":
            continue
        if r.get("handoff"):
            continue
        decided = r.get("decided_week")
        if not decided:
            continue
        if weeks_ago(decided, ref_week) >= stale_weeks:
            out.append(r)
    out.sort(key=lambda r: (r.get("decided_week", ""), r.get("title", "")))
    return out
