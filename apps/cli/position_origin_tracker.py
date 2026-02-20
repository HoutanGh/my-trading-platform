from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from apps.core.orders.events import OrderFilled
from apps.core.strategies.breakout.events import BreakoutConfirmed


class PositionOriginTracker:
    def __init__(self, *, log_path: Optional[str] = None) -> None:
        self._tags_by_account_symbol: dict[tuple[str, str], str] = {}
        self._tags_by_symbol: dict[str, str] = {}
        self._exits_by_account_symbol: dict[tuple[str, str], tuple[Optional[float], Optional[float]]] = {}
        self._exits_by_symbol: dict[str, tuple[Optional[float], Optional[float]]] = {}
        self._tps_by_account_symbol: dict[tuple[str, str], list[float]] = {}
        self._tps_by_symbol: dict[str, list[float]] = {}
        self._log_path = log_path

    def handle_event(self, event: object) -> None:
        if not isinstance(event, OrderFilled):
            if isinstance(event, BreakoutConfirmed):
                take_profit = event.take_profit
                if event.take_profits:
                    take_profit = event.take_profits[0]
                self._record_exits(
                    event.symbol,
                    take_profit,
                    event.stop_loss,
                    account=event.account,
                    take_profits=event.take_profits,
                )
            return
        self._record_tag(event.spec.account, event.spec.symbol, event.spec.client_tag)

    def tag_for(self, account: Optional[str], symbol: str) -> Optional[str]:
        sym = symbol.strip().upper()
        if account:
            tag = self._tags_by_account_symbol.get((account, sym))
            if tag:
                return tag
        return self._tags_by_symbol.get(sym)

    def exit_levels_for(
        self,
        account: Optional[str],
        symbol: str,
    ) -> tuple[Optional[float], Optional[float]]:
        sym = symbol.strip().upper()
        if account:
            levels = self._exits_by_account_symbol.get((account, sym))
            if levels:
                return levels
        return self._exits_by_symbol.get(sym, (None, None))

    def take_profits_for(
        self,
        account: Optional[str],
        symbol: str,
    ) -> Optional[list[float]]:
        sym = symbol.strip().upper()
        if account:
            levels = self._tps_by_account_symbol.get((account, sym))
            if levels:
                return list(levels)
        levels = self._tps_by_symbol.get(sym)
        return list(levels) if levels else None

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
                elif event_type == "BreakoutConfirmed":
                    take_profits = event.get("take_profits")
                    take_profit = (
                        take_profits[0]
                        if isinstance(take_profits, list) and take_profits
                        else event.get("take_profit")
                    )
                    self._record_exits(
                        event.get("symbol", ""),
                        take_profit,
                        event.get("stop_loss"),
                        account=event.get("account"),
                        take_profits=take_profits if isinstance(take_profits, list) else None,
                    )
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

    def _record_exits(
        self,
        symbol: str,
        take_profit: Optional[float],
        stop_loss: Optional[float],
        *,
        account: Optional[str] = None,
        take_profits: Optional[list[float]] = None,
    ) -> bool:
        if take_profit is None and stop_loss is None:
            return False
        sym = symbol.strip().upper()
        if not sym:
            return False
        levels = (take_profit, stop_loss)
        changed = False
        acct = (account or "").strip()
        if acct:
            if self._exits_by_account_symbol.get((acct, sym)) != levels:
                self._exits_by_account_symbol[(acct, sym)] = levels
                changed = True
        if self._exits_by_symbol.get(sym) != levels:
            self._exits_by_symbol[sym] = levels
            changed = True
        normalized_tps: list[float] = []
        if take_profits:
            for item in take_profits:
                parsed = _coerce_float(item)
                if parsed is None:
                    continue
                normalized_tps.append(parsed)
        elif take_profit is not None:
            parsed_take_profit = _coerce_float(take_profit)
            if parsed_take_profit is not None:
                normalized_tps.append(parsed_take_profit)
        if normalized_tps:
            if acct:
                existing = self._tps_by_account_symbol.get((acct, sym))
                if existing != normalized_tps:
                    self._tps_by_account_symbol[(acct, sym)] = list(normalized_tps)
                    changed = True
            existing_symbol = self._tps_by_symbol.get(sym)
            if existing_symbol != normalized_tps:
                self._tps_by_symbol[sym] = list(normalized_tps)
                changed = True
        return changed


def _coerce_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_filled_status(status: object) -> bool:
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}
