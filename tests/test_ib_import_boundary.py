from __future__ import annotations

from pathlib import Path
import re

_FORBIDDEN = re.compile(r"\b(?:from|import)\s+(ib_async|ib_insync)\b")
_ALLOWED_FILE = Path("apps/adapters/broker/_ib_client.py").as_posix()


def test_ib_backend_imports_are_centralized_to_shim() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    for base in ("apps", "tests"):
        for path in (repo_root / base).rglob("*.py"):
            rel = path.relative_to(repo_root).as_posix()
            if rel == _ALLOWED_FILE:
                continue
            text = path.read_text(encoding="utf-8")
            if _FORBIDDEN.search(text):
                offenders.append(rel)

    assert offenders == [], (
        "Direct ib_async/ib_insync imports are only allowed in "
        f"{_ALLOWED_FILE}. Offenders: {', '.join(offenders)}"
    )
