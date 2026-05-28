"""Monthly system-self-check. Compares auto signal vs manual DAILY_LOG."""
import json
import re
import datetime
from pathlib import Path
from typing import List, Dict, Any

ROOT = Path(__file__).parent
DRIFT_VERSION = (ROOT / "VERSION").read_text().strip()
FAILURE_RATE_ALERT_THRESHOLD = 10
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
        try:
            parts = match.group(1).split()
            mname = parts[0]
            day = int(parts[1].rstrip(","))
            yr = int(parts[2])
            if yr == year and MONTH_NAMES.get(mname) == month:
                seen.add(datetime.date(yr, month, day))
        except (IndexError, ValueError):
            continue
    return sorted(seen)


def _load_signal_records(signal_path: Path, year: int, month: int,
                         meta_path: Path = None) -> List[Dict[str, Any]]:
    if not signal_path.exists():
        return []

    # Build session_id -> extracted_at lookup from lineage file
    meta_extracted_at: Dict[str, str] = {}
    if meta_path is not None and meta_path.exists():
        for line in meta_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                m = json.loads(line)
                sid = m.get("session_id")
                if sid:
                    meta_extracted_at[sid] = m.get("extracted_at", "")
            except json.JSONDecodeError:
                continue

    out = []
    for line in signal_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts_str = rec.get("started_at") or meta_extracted_at.get(rec.get("session_id"), "")
        if not ts_str:
            continue
        try:
            t = datetime.datetime.fromisoformat(ts_str.replace("Z", ""))
        except ValueError:
            continue

        if t.year == year and t.month == month:
            out.append(rec)
    return out


def _load_meta_records(meta_path: Path, year: int, month: int) -> List[Dict[str, Any]]:
    """Return meta ledger records whose extracted_at falls in the given year/month.

    Records with missing or unparseable extracted_at are silently skipped.
    Returns [] if the file is absent.
    """
    if not meta_path.exists():
        return []
    out = []
    for line in meta_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = rec.get("extracted_at", "")
        if not ts_str:
            continue
        try:
            t = datetime.datetime.fromisoformat(ts_str.replace("Z", ""))
        except ValueError:
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
    # ok signals only — used for coverage (auto_dates) and extracted-record count
    signals = _load_signal_records(
        project_dir / "log" / "signal.jsonl",
        year, m,
        meta_path=project_dir / "log" / "signal.meta.jsonl",
    )
    # complete operational ledger — used for failure rate (D2)
    attempts = _load_meta_records(project_dir / "log" / "signal.meta.jsonl", year, m)

    auto_dates = set()
    for s in signals:
        try:
            t = datetime.datetime.fromisoformat(s.get("started_at", "").replace("Z", ""))
            auto_dates.add(t.date())
        except ValueError:
            pass

    missing_auto = sorted(d for d in manual_dates if d not in auto_dates)
    extra_auto = sorted(d for d in auto_dates if d not in set(manual_dates))
    skipped = [a for a in attempts if a.get("extraction_status") == "skipped_too_large"]
    skipped_sessions = {a.get("session_id") for a in skipped}
    n_skipped = len(skipped_sessions)
    considered = [a for a in attempts if a.get("extraction_status") != "skipped_too_large"]
    failures = [a for a in considered if a.get("extraction_status") != "ok"]
    failure_rate = (len(failures) / len(considered) * 100) if considered else 0

    out_dir = project_dir / "log" / "system-drift"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}-M{m:02d}.md"

    if not attempts:
        failure_line = "- No meta ledger found for this month — failure rate unavailable."
    elif not considered:
        failure_line = f"- 0/0 extractions failed/malformed — all {n_skipped} transcript(s) were skipped (too large)."
    else:
        failure_line = f"- {len(failures)}/{len(considered)} extractions failed/malformed ({failure_rate:.1f}%)"

    coverage_lines = [
        f"- {len(manual_dates)} manual log entries, {len(signals)} extracted records",
        f"- {len(missing_auto)} manual day(s) with no extracted signal: " +
        (", ".join(d.isoformat() for d in missing_auto) if missing_auto else "(none)"),
        f"- {len(extra_auto)} extracted day(s) with no manual entry: " +
        (", ".join(d.isoformat() for d in extra_auto) if extra_auto else "(none)"),
    ]
    if skipped:
        coverage_lines.append(f"- {n_skipped} transcript(s) skipped this month (too large to extract)")

    body = [
        f"# System Drift Report — {month}",
        f"",
        f"**Generated:** {datetime.date.today().isoformat()} by drift_monitor v{DRIFT_VERSION}",
        f"",
        f"## Coverage",
    ] + coverage_lines + [
        f"",
        f"## Failure rate",
        failure_line,
        f"",
        f"## Recommendations",
    ]
    if missing_auto:
        body.append(
            "- Investigate extractor gaps on the missing days. "
            "Likely causes: Claude Code restart, transcript truncated, or cron didn't run that week."
        )
    if failure_rate > FAILURE_RATE_ALERT_THRESHOLD:
        body.append(
            f"- Failure rate above {FAILURE_RATE_ALERT_THRESHOLD}% — review error.log for patterns; "
            f"consider extractor prompt review."
        )
    if not (missing_auto or failure_rate > FAILURE_RATE_ALERT_THRESHOLD):
        body.append("- No drift signals worth acting on this month.")

    out_path.write_text("\n".join(body) + "\n")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Build the monthly drift report comparing auto vs manual logs.",
    )
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--month", type=str, default=None,
                        help="YYYY-MM; default = last completed month")
    args = parser.parse_args()
    if args.month:
        month = args.month
    else:
        today = datetime.date.today().replace(day=1)
        last = today - datetime.timedelta(days=1)
        month = f"{last.year}-{last.month:02d}"
    out = build_drift_report(args.project_dir, month=month)
    print(f"wrote {out}")
