"""Build weekly bird's-eye report from signal.jsonl + DAILY_LOG + PATCH_LIST."""
import datetime
import json
from collections import Counter
from pathlib import Path
from typing import Tuple, List, Dict, Any

import plotly.graph_objects as go
from plotly.offline import plot

ROOT = Path(__file__).parent
AGGREGATOR_VERSION = (ROOT / "VERSION").read_text().strip()


def _load_signals(signal_path: Path) -> List[Dict[str, Any]]:
    if not signal_path.exists():
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    for line in signal_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("session_id")
        # latest-wins dedup
        if sid not in seen or rec.get("ended_at", "") >= seen[sid].get("ended_at", ""):
            seen[sid] = rec
    return list(seen.values())


def _isoweek_range(week: str) -> Tuple[datetime.datetime, datetime.datetime]:
    """Return [start, end) datetimes for an ISO week like '2026-W19'."""
    year, w = week.split("-W")
    start = datetime.datetime.fromisocalendar(int(year), int(w), 1)
    end = start + datetime.timedelta(days=7)
    return start, end


def _current_week() -> str:
    today = datetime.date.today()
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


def _filter_week(signals: List[Dict[str, Any]], week: str) -> List[Dict[str, Any]]:
    start, end = _isoweek_range(week)
    out = []
    for s in signals:
        try:
            t = datetime.datetime.fromisoformat(s.get("started_at", "").replace("Z", ""))
        except ValueError:
            continue
        if start <= t < end:
            out.append(s)
    return out


def _section_pace(week_signals: List[Dict[str, Any]], all_signals: List[Dict[str, Any]]) -> str:
    week_hours = sum(s.get("duration_min", 0) for s in week_signals) / 60.0
    cumulative_hours = sum(s.get("duration_min", 0) for s in all_signals) / 60.0
    return (
        f"## Pace\n\n"
        f"- This week: **{week_hours:.1f} hrs**\n"
        f"- Target: 15-20 hrs (sprint) / 10 hrs (baseline)\n"
        f"- Cumulative phase hours: {cumulative_hours:.1f}\n"
    )


def _section_solid_longest(all_signals: List[Dict[str, Any]]) -> str:
    last_touch: Dict[str, str] = {}
    for s in all_signals:
        for d in s.get("patch_list_deltas_inferred", []) or []:
            if d.get("to") == "🟢":
                last_touch[d["topic"]] = max(last_touch.get(d["topic"], ""), s.get("ended_at", ""))
    today = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    lines = ["## What's been solid longest", ""]
    rows = []
    for topic, ts in last_touch.items():
        try:
            days = (today - datetime.datetime.fromisoformat(ts.replace("Z", ""))).days
        except ValueError:
            continue
        rows.append((days, topic))
    rows.sort(reverse=True)
    if not rows:
        lines.append("_(no 🟢 items yet)_")
    for days, topic in rows[:8]:
        decay = " ← decay candidate" if days >= 14 else ""
        lines.append(f"- {topic}: {days}d{decay}")
    return "\n".join(lines) + "\n"


def _section_slipped() -> str:
    return "## What slipped or stalled\n\n_(Stage 2 retention probes will populate this section)_\n"


def _section_trajectory(all_signals: List[Dict[str, Any]]) -> str:
    week_buckets: Dict[str, Dict[str, Any]] = {}
    for s in all_signals:
        try:
            t = datetime.datetime.fromisoformat(s.get("started_at", "").replace("Z", ""))
        except ValueError:
            continue
        y, w, _ = t.isocalendar()
        key = f"{y}-W{w:02d}"
        b = week_buckets.setdefault(key, {"hours": 0.0, "topics": set(), "new_green": 0})
        b["hours"] += s.get("duration_min", 0) / 60.0
        b["topics"].update(s.get("topics", []) or [])
        for d in s.get("patch_list_deltas_inferred", []) or []:
            if d.get("to") == "🟢":
                b["new_green"] += 1
    lines = ["## Trajectory", "", "| Week | Hours | Topics touched | New 🟢 |", "|---|---|---|---|"]
    for k in sorted(week_buckets.keys()):
        b = week_buckets[k]
        lines.append(f"| {k} | {b['hours']:.1f} | {len(b['topics'])} | {b['new_green']} |")
    return "\n".join(lines) + "\n"


def _section_patterns(week_signals: List[Dict[str, Any]]) -> str:
    hints = Counter()
    struggles = Counter()
    for s in week_signals:
        for h in s.get("user_preference_hints", []) or []:
            hints[h] += 1
        for st in s.get("struggle_markers", []) or []:
            struggles[st] += 1
    out = ["## Patterns", ""]
    if hints:
        out.append("**Preference hints this week:**")
        for h, c in hints.most_common(5):
            out.append(f"- {h} ({c}×)")
    if struggles:
        out.append("\n**Recurring struggle markers:**")
        for st, c in struggles.most_common(5):
            out.append(f"- {st} ({c}×)")
    if not hints and not struggles:
        out.append("_(no notable patterns this week)_")
    return "\n".join(out) + "\n"


def _lineage_footer(week_signals: List[Dict[str, Any]]) -> str:
    failed = sum(1 for s in week_signals if s.get("extraction_status") != "ok")
    return (f"\n---\n"
            f"*Generated {datetime.date.today().isoformat()} by aggregator v{AGGREGATOR_VERSION}. "
            f"{len(week_signals) - failed}/{len(week_signals)} sessions extracted ok this week.*\n")


def build_report(project_dir: Path, week: str = None) -> Tuple[Path, Path]:
    """Build weekly markdown + HTML report. Returns (md_path, html_path)."""
    week = week or _current_week()
    signal_path = project_dir / "log" / "signal.jsonl"
    all_signals = _load_signals(signal_path)

    out_dir = project_dir / "log" / "bird-eye"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{week}.md"
    html_path = out_dir / f"{week}.html"

    if not all_signals:
        md_path.write_text(
            f"# Bird's-eye — {week}\n\n"
            f"_Bootstrapping. Need ≥ 2 sessions of extracted signal before pacing/retention "
            f"analysis is meaningful. Currently 0 records in signal.jsonl._\n"
        )
        html_path.write_text("<html><body><h1>Bootstrapping</h1></body></html>")
        return md_path, html_path

    week_signals = _filter_week(all_signals, week)
    md = (
        f"# Bird's-eye — {week}\n\n"
        + _section_pace(week_signals, all_signals)
        + "\n" + _section_solid_longest(all_signals)
        + "\n" + _section_slipped()
        + "\n" + _section_trajectory(all_signals)
        + "\n" + _section_patterns(week_signals)
        + _lineage_footer(week_signals)
    )
    md_path.write_text(md)
    html_path.write_text(_render_html(
        week=week,
        markdown_text=md,
        all_signals=all_signals,
        footer=f"aggregator v{AGGREGATOR_VERSION} · {len(week_signals)} sessions this week",
    ))
    return md_path, html_path


def _build_hours_chart(all_signals: List[Dict[str, Any]]) -> str:
    weeks: Dict[str, float] = {}
    for s in all_signals:
        try:
            t = datetime.datetime.fromisoformat(s.get("started_at", "").replace("Z", ""))
        except ValueError:
            continue
        y, w, _ = t.isocalendar()
        key = f"{y}-W{w:02d}"
        weeks[key] = weeks.get(key, 0) + s.get("duration_min", 0) / 60.0
    xs = sorted(weeks.keys())[-8:]
    ys = [weeks[k] for k in xs]
    fig = go.Figure(go.Bar(x=xs, y=ys, marker_color="#1e6fd9"))
    fig.update_layout(title="Hours per week (last 8 weeks)", height=320,
                      margin=dict(l=40, r=20, t=40, b=40), plot_bgcolor="#fafaf7")
    return plot(fig, include_plotlyjs="inline", output_type="div")


def _build_decay_heatmap(all_signals: List[Dict[str, Any]]) -> str:
    last_touch: Dict[str, datetime.datetime] = {}
    for s in all_signals:
        for d in s.get("patch_list_deltas_inferred", []) or []:
            if d.get("to") == "🟢":
                try:
                    t = datetime.datetime.fromisoformat(s.get("ended_at", "").replace("Z", ""))
                except ValueError:
                    continue
                if d["topic"] not in last_touch or t > last_touch[d["topic"]]:
                    last_touch[d["topic"]] = t
    today = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    topics = sorted(last_touch.keys(), key=lambda t: last_touch[t])
    days = [(today - last_touch[t]).days for t in topics]
    fig = go.Figure(go.Bar(x=days, y=topics, orientation="h",
                           marker_color=["#b91c1c" if d >= 14 else "#059669" for d in days]))
    fig.update_layout(title="Time since last touch (🟢 items)", height=320,
                      margin=dict(l=160, r=20, t=40, b=40), plot_bgcolor="#fafaf7",
                      xaxis_title="days")
    return plot(fig, include_plotlyjs=False, output_type="div")


_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Bird's-eye {week}</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#fafaf7;color:#1a1a1a;
     max-width:1100px;margin:0 auto;padding:24px;line-height:1.55}}
h1{{margin-top:0}} h2{{border-top:1px solid #d8d4cc;padding-top:16px;margin-top:32px}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
pre{{background:#f4f1ea;padding:12px;border-radius:5px;font-size:13px;white-space:pre-wrap}}
.footer{{color:#666;font-size:12.5px;margin-top:32px;padding-top:12px;border-top:1px solid #d8d4cc}}
</style></head><body>
<h1>Bird's-eye — {week}</h1>
<div class="charts">{hours_chart}{decay_chart}</div>
<h2>Report (markdown)</h2>
<pre>{markdown_text}</pre>
<div class="footer">{footer}</div>
</body></html>"""


def _render_html(week: str, markdown_text: str, all_signals: List[Dict[str, Any]],
                 footer: str) -> str:
    return _HTML_TEMPLATE.format(
        week=week,
        hours_chart=_build_hours_chart(all_signals),
        decay_chart=_build_decay_heatmap(all_signals),
        markdown_text=markdown_text.replace("<", "&lt;").replace(">", "&gt;"),
        footer=footer,
    )
