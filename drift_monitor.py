"""Monthly system-self-check. Compares auto signal vs manual DAILY_LOG."""
import json
import re
import datetime
from pathlib import Path
from typing import List, Dict, Any

ROOT = Path(__file__).parent
DRIFT_VERSION = (ROOT / "VERSION").read_text().strip()
DAILY_LOG_HEADER_RE = re.compile(r"^###\s+(\w+\s+\d+,\s+\d{4})", re.M)
MONTH_NAMES = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}


def _parse_daily_log_dates(log_text: str, year: int, month: int) -> List[datetime.date]:
    """Return unique dates that appear in manual log headers for the given year/month.

    Handles headers like:
      ### May 10, 2026
      ### May 9, 2026 — Evening session   (extra suffix ignored by regex)
    Multiple headers for the same date (morning/evening) are deduplicated.
    """
    seen = set()
    for match in DAILY_LOG_HEADER_RE.finditer(log_text):
        parts = match.group(1).split()
        try:
            mname = parts[0]
            day = int(parts[1].rstrip(","))
            yr = int(parts[2])
        except (IndexError, ValueError):
            continue
        if yr == year and MONTH_NAMES.get(mname) == month:
            seen.add(datetime.date(yr, month, day))
    return sorted(seen)


def _load_signal_records(signal_path: Path, year: int, month: int) -> List[Dict[str, Any]]:
    if not signal_path.exists():
        return []
    out = []
    for line in signal_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            t = datetime.datetime.fromisoformat(rec.get("started_at", "").replace("Z", ""))
        except (json.JSONDecodeError, ValueError):
            continue
        if t.year == year and t.month == month:
            out.append(rec)
    return out


def build_drift_report(project_dir: Path, month: str) -> Path:
    """Build a monthly drift report. month is 'YYYY-MM'.

    Returns the Path to the written markdown report.
    Output: <project_dir>/log/system-drift/YYYY-Mxx.md
    """
    year, m = map(int, month.split("-"))
    log_path = project_dir / "log" / "DAILY_LOG.md"
    log_text = log_path.read_text() if log_path.exists() else ""
    manual_dates = _parse_daily_log_dates(log_text, year, m)
    signals = _load_signal_records(project_dir / "log" / "signal.jsonl", year, m)

    auto_dates = set()
    for s in signals:
        try:
            t = datetime.datetime.fromisoformat(s.get("started_at", "").replace("Z", ""))
            auto_dates.add(t.date())
        except ValueError:
            pass

    missing_auto = sorted(d for d in manual_dates if d not in auto_dates)
    extra_auto = sorted(d for d in auto_dates if d not in set(manual_dates))
    failures = [s for s in signals if s.get("extraction_status") != "ok"]
    failure_rate = (len(failures) / len(signals) * 100) if signals else 0

    out_dir = project_dir / "log" / "system-drift"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}-M{m:02d}.md"

    body = [
        f"# System Drift Report — {month}",
        f"",
        f"**Generated:** {datetime.date.today().isoformat()} by drift_monitor v{DRIFT_VERSION}",
        f"",
        f"## Coverage",
        f"- {len(manual_dates)} manual log entries, {len(signals)} extracted records",
        f"- {len(missing_auto)} manual day(s) with no extracted signal: " +
        (", ".join(d.isoformat() for d in missing_auto) if missing_auto else "(none)"),
        f"- {len(extra_auto)} extracted day(s) with no manual entry: " +
        (", ".join(d.isoformat() for d in extra_auto) if extra_auto else "(none)"),
        f"",
        f"## Failure rate",
        f"- {len(failures)}/{len(signals)} extractions failed/malformed ({failure_rate:.1f}%)",
        f"",
        f"## Recommendations",
    ]
    if missing_auto:
        body.append(
            "- Investigate extractor gaps on the missing days. "
            "Likely causes: Claude Code restart, transcript truncated, or cron didn't run that week."
        )
    if failure_rate > 10:
        body.append(
            f"- Failure rate above 10% — review error.log for patterns; "
            f"consider extractor prompt review."
        )
    if not (missing_auto or failure_rate > 10):
        body.append("- No drift signals worth acting on this month.")

    out_path.write_text("\n".join(body) + "\n")
    return out_path
