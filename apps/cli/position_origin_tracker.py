from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from apps.core.orders.events import OrderFilled


class PositionOriginTracker:
    def __init__(self, *, log_path: Optional[str] = None) -> None:
        self._tags_by_account_symbol: dict[tuple[str, str], str] = {}
        self._tags_by_symbol: dict[str, str] = {}
        self._log_path = log_path

    def handle_event(self, event: object) -> None:
        if not isinstance(event, OrderFilled):
            return
        self._record_tag(event.spec.account, event.spec.symbol, event.spec.client_tag)

    def tag_for(self, account: Optional[str], symbol: str) -> Optional[str]:
        sym = symbol.strip().upper()
        if account:
            tag = self._tags_by_account_symbol.get((account, sym))
            if tag:
                return tag
        return self._tags_by_symbol.get(sym)

    async def seed_from_ibkr(self, ib, *, timeout: float) -> int:
        fills = await asyncio.wait_for(ib.reqExecutionsAsync(), timeout=timeout)
        added = 0
        for fill in fills:
            execution = getattr(fill, "execution", None)
            contract = getattr(fill, "contract", None)
            if execution is None or contract is None:
                continue
            order_ref = getattr(execution, "orderRef", None)
            if not order_ref:
                continue
            symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None) or ""
            account = getattr(execution, "acctNumber", None)
            if self._record_tag(account, symbol, str(order_ref)):
                added += 1
        return added

    def seed_from_jsonl(self, path: Optional[str] = None) -> int:
        log_path = path or self._log_path
        if not log_path:
            return 0
        log_path = os.path.expanduser(log_path)
        if not os.path.exists(log_path):
            return 0

        added = 0
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = payload.get("event_type")
                event = payload.get("event", {})
                spec = event.get("spec", {}) if isinstance(event, dict) else {}
                if event_type == "OrderFilled":
                    if self._record_tag(
                        spec.get("account"),
                        spec.get("symbol", ""),
                        spec.get("client_tag"),
                    ):
                        added += 1
                elif event_type == "OrderStatusChanged":
                    status = event.get("status")
                    if _is_filled_status(status):
                        if self._record_tag(
                            spec.get("account"),
                            spec.get("symbol", ""),
                            spec.get("client_tag"),
                        ):
                            added += 1
        return added

    def _record_tag(self, account: Optional[str], symbol: str, tag: Optional[str]) -> bool:
        if not tag:
            return False
        sym = symbol.strip().upper()
        if not sym:
            return False
        acct = (account or "").strip()
        changed = False
        if acct:
            if self._tags_by_account_symbol.get((acct, sym)) != tag:
                self._tags_by_account_symbol[(acct, sym)] = tag
                changed = True
        if self._tags_by_symbol.get(sym) != tag:
            self._tags_by_symbol[sym] = tag
            changed = True
        return changed


def _is_filled_status(status: object) -> bool:
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}
