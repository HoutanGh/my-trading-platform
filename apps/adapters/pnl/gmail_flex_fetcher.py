from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
import io
import zipfile

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass(frozen=True)
class GmailFlexConfig:
    client_secret_path: Path
    token_path: Path
    save_dir: Path
    lookback_days: int
    sender: str
    subject_contains: str
    filename_prefix: str
    query: Optional[str] = None

    @classmethod
    def from_env(cls) -> "GmailFlexConfig":
        client_secret = os.getenv("GMAIL_CLIENT_SECRET_PATH")
        if not client_secret:
            raise RuntimeError("GMAIL_CLIENT_SECRET_PATH is not set")
        token_path = os.getenv("GMAIL_TOKEN_PATH", "data/creds/gmail_token.json")
        save_dir = os.getenv("IBKR_FLEX_SAVE_DIR", "data/raw/ikbr_flex")
        lookback_days = int(os.getenv("GMAIL_LOOKBACK_DAYS", "30"))
        sender = os.getenv(
            "GMAIL_SENDER",
            "donotreply@interactivebrokers.com",
        )
        subject_contains = os.getenv("GMAIL_SUBJECT_CONTAINS", "Activity Flex")
        filename_prefix = os.getenv(
            "GMAIL_FILENAME_PREFIX",
            "flex.1332995.Daily_PL.",
        )
        query = os.getenv("GMAIL_QUERY")
        return cls(
            client_secret_path=Path(client_secret).expanduser(),
            token_path=Path(token_path).expanduser(),
            save_dir=Path(save_dir).expanduser(),
            lookback_days=lookback_days,
            sender=sender,
            subject_contains=subject_contains,
            filename_prefix=filename_prefix,
            query=query,
        )


def fetch_latest_flex_report(config: GmailFlexConfig) -> Path:
    service = _get_gmail_service(
        config.client_secret_path,
        config.token_path,
    )
    query = config.query or _build_query(config)
    logger.info("Gmail flex query=%s", query)
    messages = _list_messages(service, query=query, max_results=25)
    if not messages:
        raise RuntimeError("No Gmail messages found for Flex query.")

    best = _find_latest_attachment(
        service=service,
        message_ids=messages,
        filename_prefix=config.filename_prefix,
    )
    if not best:
        raise RuntimeError("No Flex CSV attachment found in recent messages.")

    message_id, attachment_id, filename, internal_date = best
    raw = _download_attachment(service, message_id, attachment_id)
    saved_path = _save_attachment_bytes(
        raw_bytes=raw,
        filename=filename,
        save_dir=config.save_dir,
        filename_prefix=config.filename_prefix,
    )
    logger.info(
        "Saved flex report file=%s message_date=%s",
        saved_path,
        _format_internal_date(internal_date),
    )
    return saved_path


def _get_gmail_service(client_secret_path: Path, token_path: Path):
    creds: Optional[Credentials] = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_path),
                SCOPES,
            )
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _build_query(config: GmailFlexConfig) -> str:
    query = (
        f'from:{config.sender} subject:"{config.subject_contains}" '
        f'has:attachment newer_than:{config.lookback_days}d'
    )
    if config.filename_prefix:
        # Gmail filename search is substring-based; this narrows results.
        query = f'{query} filename:{config.filename_prefix}'
    return query


def _list_messages(service, *, query: str, max_results: int) -> list[str]:
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return [item["id"] for item in resp.get("messages", [])]


def _find_latest_attachment(
    *,
    service,
    message_ids: Iterable[str],
    filename_prefix: str,
) -> Optional[tuple[str, str, str, int]]:
    best: Optional[tuple[str, str, str, int]] = None
    for msg_id in message_ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        internal_date = int(msg.get("internalDate", "0"))
        payload = msg.get("payload", {})
        for filename, attachment_id in _iter_attachments(payload):
            if not _is_candidate_filename(filename, filename_prefix):
                continue
            candidate = (msg_id, attachment_id, filename, internal_date)
            if best is None or candidate[3] > best[3]:
                best = candidate
    return best


def _iter_attachments(payload: dict) -> Iterable[tuple[str, str]]:
    filename = payload.get("filename")
    body = payload.get("body", {})
    attachment_id = body.get("attachmentId")
    if filename and attachment_id:
        yield filename, attachment_id
    for part in payload.get("parts", []) or []:
        yield from _iter_attachments(part)


def _is_candidate_filename(filename: str, filename_prefix: str) -> bool:
    if not filename.lower().endswith((".csv", ".zip")):
        return False
    if not filename_prefix:
        return "daily_pl" in filename.lower()
    return filename.startswith(filename_prefix)


def _download_attachment(service, message_id: str, attachment_id: str) -> bytes:
    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = attachment.get("data")
    if not data:
        raise RuntimeError("Attachment payload was empty.")
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def _save_attachment_bytes(
    *,
    raw_bytes: bytes,
    filename: str,
    save_dir: Path,
    filename_prefix: str,
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    if filename.lower().endswith(".zip"):
        csv_name, csv_bytes = _extract_csv_from_zip(raw_bytes, filename_prefix)
        filename = csv_name
        raw_bytes = csv_bytes
    save_path = save_dir / filename
    save_path.write_bytes(raw_bytes)
    return save_path


def _extract_csv_from_zip(raw_bytes: bytes, filename_prefix: str) -> tuple[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        candidates = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if filename_prefix:
            candidates = [name for name in candidates if Path(name).name.startswith(filename_prefix)]
        if not candidates:
            raise RuntimeError("Zip attachment did not contain a matching CSV file.")
        chosen = sorted(candidates)[-1]
        return Path(chosen).name, zf.read(chosen)


def _format_internal_date(internal_date_ms: int) -> str:
    dt = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)
    return dt.isoformat()
