# learning-system

User-level tooling that auto-extracts signal from Claude Code transcripts and produces a weekly bird's-eye dashboard per opted-in project.

## Layout

- `extractor.py` — per-session signal extraction via the Claude Code CLI
- `aggregator.py` — weekly markdown + HTML report builder
- `drift_monitor.py` — monthly system-self-check
- `lib/` — shared helpers (slug, state)
- `prompts/` — versioned extraction prompts
- `fixtures/` — saved transcripts for regression testing
- `tests/` — pytest suite
- `cron/` — crontab template (deprecated)
- `launchd/` — LaunchAgent plist templates (active scheduler)
- `state.json` — per-session checkpoint (gitignored)
- `projects.txt` — opt-in registry, one path per line (gitignored)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "$HOME/Documents/code/ai-inference-track" > projects.txt
./install_launchd.sh   # macOS LaunchAgent — has Keychain access for claude CLI auth
```

Note: macOS cron cannot access Keychain in its security context, so the claude CLI auth fails. LaunchAgents run in the user session with full Keychain access. `install_cron.sh` is kept as a fallback but deprecated.

The extractor uses the Claude Code CLI (`claude`) under the hood — no API key needed. Uses your Claude Code subscription auth from `~/.claude/`.

## Event-driven extraction (v0.3.0)

The extractor is triggered by a Claude Code **SessionStart hook**, not the 2am clock:
when you start a session in an opted-in project, `trigger.py` runs `launchctl start`
on the extractor LaunchAgent, which extracts detached in your (awake, Keychain-unlocked)
session. This fixes the unattended-2am hang on the macOS permission prompt. The launchd
`StartCalendarInterval` is kept as a **weekly** (Sunday 02:00) safety net in case the hook
ever stops firing. `install_launchd.sh` registers the hook; remove it with
`./.venv/bin/python install_hook.py uninstall`.

## Manual usage

```bash
# Extract any pending transcripts now
.venv/bin/python extractor.py --scan-all

# Build report for current week
.venv/bin/python aggregator.py --project-dir ~/Documents/code/ai-inference-track

# Build last-month drift report
.venv/bin/python drift_monitor.py --project-dir ~/Documents/code/ai-inference-track
```

## Tests

```bash
.venv/bin/pytest -v
```

## Model

Default is `claude-opus-4-7` (1M context) via the Claude Code CLI. Uses your
Claude Code subscription quota — no separate API key needed. Opus is chosen
for extraction quality on long, nuanced learning sessions. To switch to a
cheaper/faster model, edit `MODEL` in `extractor.py` (e.g. `claude-haiku-4-5`
for cheap, but capped at ~720KB transcripts).

## Versioning

Bump `VERSION` (semver) when changing extractor prompts or signal schema.
Version is stamped on every signal lineage record and every aggregated report.
A version bump triggers automatic re-extraction of all transcripts on next scheduled run.

## Drift monitoring

`drift_monitor.py` runs monthly via launchd and produces `log/system-drift/YYYY-Mxx.md`
in each opted-in project. Run `/system-review` in Claude Code on the first
weekend of each month to walk through findings.

## Where data lives

- Code: `~/.claude/bin/learning-system/`
- Data: `<project>/log/signal.jsonl`, `signal.meta.jsonl`, `bird-eye/`, `system-drift/`
- Transcripts (read-only): `~/.claude/projects/<slug>/`

## Changelog

- `0.2.0` — extraction reliability: failure observability in meta (error/raw_response/stop_reason/api_error_status/attempts), bounded per-call retry, robust JSON parse, non-ok kept out of signal.jsonl; drift_monitor failure-rate now meta-sourced and excludes skipped_too_large; aggregator weekly footer reports extracted-signal count.
  Note: a chunked session with some failed chunks is recorded as a partial success and is not re-attempted until the next extractor_version bump (retry is within-run only).
- `0.1.0` — initial release

## See also

- Design spec: `~/Documents/code/ai-inference-track/plan/specs/2026-05-12-self-learning-system-stage-1.html`
- Implementation plan: `~/Documents/code/ai-inference-track/plan/plans/2026-05-12-self-learning-system-stage-1.html`
