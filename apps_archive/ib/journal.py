import json
from datetime import datetime, timezone
from pathlib import Path

JOURNAL_DIR = Path(__file__).resolve().parent / "journal"
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
JOURNAL_FILE = JOURNAL_DIR / "events.jsonl"

def _utc_iso() -> str:
    """Get the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()

def write_event(event: dict) -> None:
    """Write an event to the journal file in JSON Lines format."""
    event = {"ts": _utc_iso(), **event}

    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")