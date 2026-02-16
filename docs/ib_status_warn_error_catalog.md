# IB Status/Warn/Error Catalog (`apps/`)

This is the current catalog of IB-facing status/warn/error outputs implemented in `apps/`.
It reflects code behavior only; IBKR can emit additional codes/messages that are not explicitly handled.

## 1) `IbStatus`, `IbWarn`, `IbError` (CLI labels)

Source: `apps/cli/event_printer.py` (`_gateway_label`, `_should_hide_gateway_log`)

These labels are printed only for `IbGatewayLog` events:

- `IbStatus`: gateway message contains `"is ok"` (case-insensitive).
- `IbWarn`: gateway message contains `"inactive"` or `"broken"` (case-insensitive), and did not match `IbStatus`.
- `IbError`: fallback for all other gateway messages.
- CLI timestamp prefix is compact `HH:MM:SS` (no microseconds/timezone suffix).

Printed line fields:

- `<ib_error_code>` or `<code>/<alias>` (if present)
- `req=<ib_req_id>` (only when req id is non-negative)
- compact message preview (if present)
- `adv` (if advanced reject payload is present)

Compact aliases currently used:

- `10197/competing_session`
- `2104/md_ok`
- `2107/hmds_inactive`
- `2106/hmds_ok`
- `2158/secdef_ok`
- `1102/restored`

Compact message previews currently used for those same codes:

- `10197`: `competing live session`
- `2104`: `market data farm ok`
- `2107`: `historical data farm inactive`
- `2106`: `historical data farm ok`
- `2158`: `sec-def data farm ok`
- `1102`: `connectivity restored`

Hidden-from-console filter:

- If `code == 162` and message contains `"query cancel"` (case-insensitive), `IbGatewayLog` is not printed in the CLI event stream.

## 2) Where `IbGatewayLog` comes from

Source: `apps/adapters/broker/ibkr_connection.py`

`IBKRConnection` wraps `ib_insync` wrapper `error(...)` callbacks and emits `IbGatewayLog` with:

- `req_id`
- `code`
- `message`
- `advanced` (advanced reject JSON/message if available)
- connection metadata (`host`, `port`, `client_id`)

Additional suppression rule at adapter layer:

- If `code == 162` and message is empty, or contains `"query cancelled"`, the original `ib_insync` error handler is suppressed.
- The gateway payload is still captured/emitted to subscribers before suppression.

## 3) IB codes with explicit platform behavior

Source: `apps/adapters/market_data/ibkr_bar_stream.py`

- `10197` (`_COMPETING_SESSION_CODE`)
  - Marks active bar streams as `blocked_competing_session`
  - Emits `BarStreamCompetingSessionBlocked`
  - Printed label: `BarStreamBlocked`

- `2104`, `2106`, `1102` (`_RECONNECT_RECOVERY_CODES`)
  - Clears `blocked_competing_session` state to `stalled`
  - Emits `BarStreamCompetingSessionCleared`
  - Printed label: `BarStreamUnblocked`
  - Triggers global recovery scan scheduling when self-heal is enabled

- `162` (query cancel/cancelled handling)
  - Special-cased in connection/event-printer suppression logic (see sections 1 and 2)

## 4) Bar stream status values you can observe

Source: `apps/adapters/market_data/ibkr_bar_stream.py` (`_StreamHealth.status`, `_status_rank`)

Runtime health states:

- `starting`
- `healthy`
- `stalled`
- `recovering`
- `failed`
- `blocked_competing_session`

These appear via:

- `breakout status` health summaries in `apps/cli/repl.py`
- health/recovery events in the event stream (`BarStreamStalled`, `BarStreamRecovered`, `BarStreamHeal`, `BarStreamHealFail`, `BarStreamBlocked`, `BarStreamUnblocked`, `BarStreamScan`)

## 5) IB connection lifecycle status/error events

Source: `apps/core/ops/events.py`, emitted by `apps/adapters/broker/ibkr_connection.py`

- `IbkrConnectionAttempt`
- `IbkrConnectionEstablished`
- `IbkrConnectionFailed` (`error_type`, `message`)
- `IbkrConnectionClosed` (`reason`)

These are event-log objects (not mapped to `IbStatus`/`IbWarn`/`IbError` labels by `event_printer`).

## 6) Order-replace IB error capture (`broker_code` / `broker_message`)

Source: `apps/adapters/broker/ibkr_order_port.py` (`_GatewayOrderErrorCapture`)

For stop-loss replace flows, gateway messages are captured when:

- gateway `req_id == order_id` being replaced

Captured values populate `LadderStopLossReplaceFailed`:

- `status` (IB order status text)
- `broker_code` (IB code)
- `broker_message` (IB message/advanced payload)

Internal status buckets used by replace logic:

- accepted: `presubmitted`, `submitted`
- inactive/terminal: `inactive`, `cancelled`, `apicancelled`, `filled`

## 7) `can-trade` warning/error outcomes (IB what-if checks)

Source: `apps/cli/repl.py` (`_check_trade_eligibility`, `_is_trade_blocking_warning`)

For IB `whatIfOrderAsync` checks:

- Statuses treated as blocked: `inactive`, `cancelled`, `apicancelled`, `rejected`
- Warning text terms treated as blocked include:
  - `not eligible`
  - `not allowed`
  - `no trading permissions`
  - `trading permissions for this contract`
  - `trading permissions for this instrument`
  - `not available for trading`
  - `restricted`
  - `prohibited`
  - `invalid destination`
  - `outside regular trading hours`

Warnings not matching the blocking list are returned as eligible with a warning detail.

## 8) Log files to inspect

Default paths from `apps/cli/__main__.py`:

- `apps/journal/ib_gateway.jsonl` (`APPS_IB_GATEWAY_LOG_PATH`): serialized `IbGatewayLog`
- `apps/journal/events.jsonl` (`APPS_EVENT_LOG_PATH` / fallback `APPV2_EVENT_LOG_PATH`): event bus stream
- `apps/journal/ops.jsonl` (`APPS_OPS_LOG_PATH`): ops/REPL error logs
- Optional raw tail sink: `apps/journal/ib_gateway_raw.jsonl` (`APPS_IB_GATEWAY_RAW_LOG_PATH`) when `IB_GATEWAY_LOG_PATH` is configured
