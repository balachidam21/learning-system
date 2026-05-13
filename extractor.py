"""Session transcript → structured signal record."""
import hashlib
import json
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

try:
    from anthropic import Anthropic
    _anthropic_client = Anthropic()
except Exception:
    _anthropic_client = None  # tests will mock

ROOT = Path(__file__).parent
PROMPT_PATH = ROOT / "prompts" / "extract.v1.txt"
PROMPT_ID = "extract.v1"
MODEL = "claude-opus-4-7"


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
        signal = json.loads(response.content[0].text)
        signal.setdefault("extraction_status", "ok")
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
