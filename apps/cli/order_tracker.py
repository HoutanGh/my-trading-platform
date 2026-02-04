from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from apps.core.orders.events import (
    OrderIdAssigned,
    OrderIntent,
    OrderSent,
    OrderStatusChanged,
)
from apps.core.orders.models import OrderSpec, OrderType


@dataclass
class OrderRecord:
    spec: OrderSpec
    order_id: int
    status: Optional[str] = None
    updated_at: Optional[datetime] = None


class OrderTracker:
    def __init__(self) -> None:
        self._pending: list[OrderSpec] = []
        self._orders: dict[int, OrderRecord] = {}

    def handle_event(self, event: object) -> None:
        if isinstance(event, (OrderIntent, OrderSent)):
            if event.spec not in self._pending:
                self._pending.append(event.spec)
            return
        if isinstance(event, OrderIdAssigned):
            self._remove_pending(event.spec)
            if event.order_id is not None:
                self._orders[event.order_id] = OrderRecord(
                    spec=event.spec,
                    order_id=event.order_id,
                    updated_at=event.timestamp,
                )
            return
        if isinstance(event, OrderStatusChanged):
            if event.order_id is None:
                return
            record = self._orders.get(event.order_id)
            if record:
                record.status = event.status
                record.updated_at = event.timestamp
            else:
                self._orders[event.order_id] = OrderRecord(
                    spec=event.spec,
                    order_id=event.order_id,
                    status=event.status,
                    updated_at=event.timestamp,
                )

    def format_lines(self, *, pending_only: bool = False) -> list[str]:
        return self.format_table(pending_only=pending_only)

    def format_table(self, *, pending_only: bool = False) -> list[str]:
        rows = self._rows(pending_only=pending_only)
        if not rows:
            return []
        headers = ["id", "side", "symbol", "qty", "type", "limit", "status"]
        widths = [len(label) for label in headers]
        for row in rows:
            for idx, value in enumerate(row):
                widths[idx] = max(widths[idx], len(value))
        header = " | ".join(label.ljust(widths[idx]) for idx, label in enumerate(headers))
        divider = "-+-".join("-" * width for width in widths)
        lines = [header, divider]
        for row in rows:
            lines.append(
                " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row))
            )
        return lines

    def get_order_record(self, order_id: int) -> Optional[OrderRecord]:
        return self._orders.get(order_id)

    def _rows(self, *, pending_only: bool) -> list[list[str]]:
        rows: list[list[str]] = []
        if not pending_only:
            for order_id in sorted(self._orders):
                record = self._orders[order_id]
                spec = record.spec
                rows.append(
                    [
                        str(order_id),
                        spec.side.value,
                        spec.symbol,
                        str(spec.qty),
                        spec.order_type.value,
                        _format_limit(spec),
                        record.status or "-",
                    ]
                )
        for spec in self._pending:
            rows.append(
                [
                    "pending",
                    spec.side.value,
                    spec.symbol,
                    str(spec.qty),
                    spec.order_type.value,
                    _format_limit(spec),
                    "pending",
                ]
            )
        return rows

    def _remove_pending(self, spec: OrderSpec) -> None:
        for idx, pending in enumerate(self._pending):
            if pending == spec:
                del self._pending[idx]
                return


def _format_limit(spec: OrderSpec) -> str:
    if spec.order_type == OrderType.LIMIT:
        return str(spec.limit_price)
    return "-"
