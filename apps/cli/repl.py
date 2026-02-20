from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import signal
import shlex
import sys
import traceback
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

try:
    import readline
except ImportError:
    readline = None

from ib_insync import MarketOrder, Stock

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.cli.event_printer import suppress_gateway_req_id
from apps.cli.order_tracker import OrderTracker
from apps.cli.position_origin_tracker import PositionOriginTracker
from apps.core.analytics.flow.take_profit import TakeProfitRequest, TakeProfitService
from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.active_orders.service import ActiveOrdersService
from apps.core.market_data.ports import BarStreamPort, QuotePort, QuoteStreamPort
from apps.core.ops.events import (
    CliErrorLogged,
    OrphanExitOrderCancelFailed,
    OrphanExitOrderCancelled,
    OrphanExitOrderDetected,
    OrphanExitReconciliationCompleted,
)
from apps.core.orders.events import (
    BracketChildOrderFilled,
    BracketChildOrderStatusChanged,
    OrderFilled,
    OrderIdAssigned,
    OrderStatusChanged,
)
from apps.core.orders.models import (
    LadderExecutionMode,
    OrderCancelSpec,
    OrderReplaceSpec,
    OrderSide,
    OrderSpec,
    OrderType,
)
from apps.core.orders.ports import EventBus
from apps.core.orders.service import OrderService, OrderValidationError
from apps.core.pnl.service import PnlService
from apps.core.positions.models import PositionSnapshot
from apps.core.positions.service import PositionsService
from apps.core.strategies.breakout.events import BreakoutStopped, BreakoutTakeProfitsUpdated
from apps.core.strategies.breakout.logic import BreakoutRuleConfig, FastEntryConfig
from apps.core.strategies.breakout.runner import BreakoutRunConfig, run_breakout

CommandHandler = Callable[[list[str], dict[str, str]], Awaitable[None]]
_BREAKOUT_STATE_VERSION = 1
_TP_MODE_TOKEN = re.compile(r"^tp-(1|2|3)$", re.IGNORECASE)
_REPLACE_ACCEPTED_STATUSES = {
    "submitted",
    "presubmitted",
    "pendingsubmit",
    "apipending",
    "filled",
    "partiallyfilled",
    "partially_filled",
}
_TRADE_BLOCKING_WARNING_TERMS = (
    "not eligible",
    "not allowed",
    "no trading permission",
    "no trading permissions",
    "no opening trades",
    "trading permissions for this contract",
    "trading permissions for this instrument",
    "not available for trading",
    "restricted",
    "prohibited",
    "invalid destination",
    "outside regular trading hours",
    "closing-only status",
)


@dataclass
class _BreakoutTpRepriceSession:
    client_tag: str
    symbol: str
    qty: int
    stop_loss: float
    tp_count: int
    bar_size: str
    use_rth: bool
    timeout_seconds: float
    account: Optional[str] = None
    take_profit_qtys: Optional[list[int]] = None
    parent_order_id: Optional[int] = None
    tp_child_order_ids: dict[int, int] = field(default_factory=dict)
    tp_filled: bool = False
    reprice_started: bool = False


@dataclass
class _BreakoutTradesSession:
    key: str
    tag: str
    symbol: str
    level: Optional[float] = None
    configured_tps: list[float] = field(default_factory=list)
    configured_sl: Optional[float] = None
    confirm_time: Optional[datetime] = None
    watcher_stop_time: Optional[datetime] = None
    watcher_stop_reason: Optional[str] = None
    entry_order_id: Optional[int] = None
    entry_side: Optional[str] = None
    planned_entry_qty: Optional[float] = None
    entry_filled_qty: float = 0.0
    entry_notional: float = 0.0
    entry_avg_price: Optional[float] = None
    entry_remaining_qty: Optional[float] = None
    entry_last_status: Optional[str] = None
    entry_first_fill_time: Optional[datetime] = None
    tp_expected_by_kind: dict[str, float] = field(default_factory=dict)
    child_status_by_kind: dict[str, str] = field(default_factory=dict)
    protection_state: Optional[str] = None
    protection_reason: Optional[str] = None
    tp_filled_qty: float = 0.0
    tp_notional: float = 0.0
    stop_filled_qty: float = 0.0
    stop_notional: float = 0.0
    first_exit_time: Optional[datetime] = None
    last_exit_time: Optional[datetime] = None
    replace_failed_count: int = 0
    last_replace_failed_status: Optional[str] = None
    last_replace_failed_code: Optional[int] = None
    last_replace_failed_message: Optional[str] = None
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: CommandHandler
    help: str
    usage: str
    aliases: tuple[str, ...] = ()


class REPL:
    def __init__(
        self,
        connection: IBKRConnection,
        order_service: Optional[OrderService] = None,
        order_tracker: Optional[OrderTracker] = None,
        pnl_service: Optional[PnlService] = None,
        positions_service: Optional[PositionsService] = None,
        active_orders_service: Optional[ActiveOrdersService] = None,
        position_origin_tracker: Optional[PositionOriginTracker] = None,
        bar_stream: Optional[BarStreamPort] = None,
        tp_service: Optional[TakeProfitService] = None,
        quote_port: Optional[QuotePort] = None,
        quote_stream: Optional[QuoteStreamPort] = None,
        event_bus: Optional[EventBus] = None,
        ops_logger: Optional[Callable[[object], None]] = None,
        *,
        prompt: str = "apps> ",
        initial_config: Optional[dict[str, str]] = None,
        account_defaults: Optional[dict[str, str]] = None,
        breakout_state_path: Optional[str] = None,
    ) -> None:
        self._connection = connection
        self._order_service = order_service
        self._order_tracker = order_tracker
        self._pnl_service = pnl_service
        self._positions_service = positions_service
        self._active_orders_service = active_orders_service
        self._position_origin_tracker = position_origin_tracker
        self._bar_stream = bar_stream
        self._tp_service = tp_service
        self._quote_port = quote_port
        self._quote_stream = quote_stream
        self._event_bus = event_bus
        self._ops_logger = ops_logger
        self._prompt = prompt
        self._config: dict[str, str] = dict(initial_config or {})
        self._account_defaults = {
            key: value.strip()
            for key, value in (account_defaults or {}).items()
            if value and value.strip()
        }
        self._account_default_values = set(self._account_defaults.values())
        resolved_state_path = breakout_state_path or _resolve_breakout_state_path()
        self._breakout_state_path = (
            Path(os.path.expanduser(resolved_state_path))
            if resolved_state_path
            else None
        )
        self._suspend_breakout_state_updates = False
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}
        self._should_exit = False
        self._breakout_tasks: dict[str, tuple[BreakoutRunConfig, asyncio.Task]] = {}
        self._tp_reprice_sessions: dict[str, _BreakoutTpRepriceSession] = {}
        self._tp_reprice_tasks: dict[str, asyncio.Task] = {}
        self._session_phase_prewarm_tasks: dict[str, asyncio.Task] = {}
        self._pnl_processes: dict[str, asyncio.subprocess.Process] = {}
        orphan_scope = os.getenv("APPS_ORPHAN_EXIT_SCOPE", "all_clients").strip().lower()
        orphan_scope = orphan_scope.replace("-", "_")
        if orphan_scope not in {"client", "all_clients"}:
            orphan_scope = "all_clients"
        self._orphan_exit_scope = orphan_scope
        orphan_action = os.getenv("APPS_ORPHAN_EXIT_ACTION", "warn").strip().lower()
        if orphan_action not in {"warn", "cancel"}:
            orphan_action = "warn"
        self._orphan_exit_action = orphan_action
        self._orphan_exit_lock = asyncio.Lock()
        self._completion_matches: list[str] = []
        if self._event_bus:
            self._event_bus.subscribe(object, self._handle_tp_reprice_event)
        self._register_commands()
        self._setup_readline()

    async def run(self) -> None:
        print("Apps CLI (type 'help' to list commands).")
        while not self._should_exit:
            try:
                line = await asyncio.to_thread(input, self._prompt)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                # Keep the REPL alive when Ctrl+C interrupts input.
                print("\nInterrupted. Type 'quit' to exit.")
                continue
            except asyncio.CancelledError:
                self._clear_cancel_state()
                print("\nInterrupted. Type 'quit' to exit.")
                continue
            line = line.strip()
            if not line:
                continue
            cmd_name, args, kwargs = self._parse_line(line)
            if cmd_name is None:
                continue
            spec = self._resolve_command(cmd_name)
            if not spec:
                print(f"Unknown command: {cmd_name}. Type 'help' to list commands.")
                continue
            await self._run_command_interruptibly(spec, args, kwargs, line)
        await self._stop_pnl_processes()
        await self._stop_breakouts(persist=True)
        await self._stop_tp_reprice_tasks()

    async def _run_command_interruptibly(
        self,
        spec: CommandSpec,
        args: list[str],
        kwargs: dict[str, str],
        raw_line: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        command_task = loop.create_task(spec.handler(args, kwargs))
        interrupted = False
        sigint_installed = False
        previous_sigint_handler: object = signal.getsignal(signal.SIGINT)

        def _cancel_command() -> None:
            nonlocal interrupted
            interrupted = True
            if not command_task.done():
                command_task.cancel()

        try:
            try:
                loop.add_signal_handler(signal.SIGINT, _cancel_command)
                sigint_installed = True
            except (NotImplementedError, RuntimeError, ValueError):
                sigint_installed = False

            try:
                await command_task
            except KeyboardInterrupt:
                interrupted = True
            except asyncio.CancelledError:
                interrupted = True
                self._clear_cancel_state()
            except Exception as exc:
                self._log_cli_error(exc, spec.name, raw_line)
                _print_exception("Command error", exc)

            if interrupted:
                await self._cancel_command_with_fallback(command_task)
                print("Command interrupted.")
        finally:
            if sigint_installed:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except Exception:
                    pass
            try:
                signal.signal(signal.SIGINT, previous_sigint_handler)
            except (TypeError, ValueError):
                pass

    async def _cancel_command_with_fallback(self, command_task: asyncio.Task[object]) -> None:
        if command_task.done():
            await asyncio.gather(command_task, return_exceptions=True)
            return

        command_task.cancel()
        if await self._wait_for_task_completion(command_task, timeout=0.75):
            return

        if self._connection.status().get("connected"):
            print("Interrupt: forcing broker disconnect to release pending command...")
            self._connection.disconnect()

        command_task.cancel()
        if await self._wait_for_task_completion(command_task, timeout=1.5):
            return

        print("Warning: command did not stop cleanly.")

    async def _wait_for_task_completion(
        self,
        task: asyncio.Task[object],
        *,
        timeout: float,
    ) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.gather(task, return_exceptions=True)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return task.done()
        return True

    def _clear_cancel_state(self) -> None:
        current = asyncio.current_task()
        if current is None:
            return
        while current.cancelling():
            current.uncancel()

    def _register_commands(self) -> None:
        self._register(
            CommandSpec(
                name="help",
                handler=self._cmd_help,
                help="Show available commands or help for a command.",
                usage="help [command]",
                aliases=("?",),
            )
        )
        self._register(
            CommandSpec(
                name="commands",
                handler=self._cmd_help,
                help="Alias for help; list available commands.",
                usage="commands",
            )
        )
        self._register(
            CommandSpec(
                name="clear",
                handler=self._cmd_clear,
                help="Clear the terminal screen.",
                usage="clear",
                aliases=("cls",),
            )
        )
        self._register(
            CommandSpec(
                name="connect",
                handler=self._cmd_connect,
                help="Connect to IBKR (paper or live).",
                usage="connect [paper|live] [host=...] [port=...] [client_id=...] [readonly=true|false] [timeout=...]",
            )
        )
        self._register(
            CommandSpec(
                name="buy",
                handler=self._cmd_buy,
                help="Submit a basic buy order (market or limit).",
                usage=(
                    "buy SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false] [account=...] [client_tag=...] "
                    "| buy SYMBOL QTY [limit=...]"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="sell",
                handler=self._cmd_sell,
                help="Submit a basic sell order (market or limit).",
                usage=(
                    "sell SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false] [account=...] [client_tag=...] "
                    "| sell SYMBOL QTY [limit=...]"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="can-trade",
                handler=self._cmd_can_trade,
                help="Check if IBKR currently accepts a what-if order for symbols.",
                usage=(
                    "can-trade SYMBOL [SYMBOL ...] [side=BUY|SELL] [qty=1] [tif=DAY] "
                    "[outside_rth=true|false] [exchange=SMART] [currency=USD] [account=...] "
                    "| can-trade symbol=AAPL,TSLA"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="breakout",
                handler=self._cmd_breakout,
                help="Start or cancel a breakout watcher.",
                usage=(
                    "breakout SYMBOL level=... qty=... [tp=...|tp=1.1-1.3|tp=auto tp_count=1|2|3] [sl=...] [rth=true|false] [bar=1 min] "
                    "[fast=true|false] [fast_bar=1 secs] [max_bars=...] [tif=DAY] [outside_rth=true|false] "
                    "[entry=limit|market] [quote_age=...] [tp_exec=attached|detached|detached70] [account=...] [client_tag=...] "
                    "| breakout SYMBOL LEVEL QTY [TP] [SL] "
                    "| breakout SYMBOL LEVEL QTY tp-2|tp-3 [SL] "
                    "| breakout status | breakout cancel [SYMBOL ...|ALL]"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="tp",
                handler=self._cmd_tp,
                help="Compute take-profit levels (analysis only).",
                usage="tp SYMBOL [bar=1 min] [rth=true|false]",
            )
        )
        self._register(
            CommandSpec(
                name="orders",
                handler=self._cmd_orders,
                help="Show tracked orders, broker active orders, or change an order.",
                usage=(
                    "orders [pending] "
                    "| orders cancel ORDER_ID "
                    "| orders replace ORDER_ID [limit=...] [qty=...] [tif=DAY] [outside_rth=true|false] "
                    "| orders broker [account=...] [scope=client|all_clients] "
                    "| orders broker cancel ORDER_ID "
                    "| orders broker cancel all [account=...] [scope=client|all_clients]"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="trades",
                handler=self._cmd_trades,
                help="Show today's app-seen fills and completed bracket trades.",
                usage="trades",
            )
        )
        self._register(
            CommandSpec(
                name="positions",
                handler=self._cmd_positions,
                help="Show current positions from IBKR.",
                usage="positions [account=...]",
                aliases=("pos",),
            )
        )
        self._register(
            CommandSpec(
                name="ingest-flex",
                handler=self._cmd_ingest_flex,
                help="Ingest a Flex CSV into daily P&L.",
                usage="ingest-flex csv=... account=... [source=flex]",
                aliases=("ingest",),
            )
        )
        self._register(
            CommandSpec(
                name="pnl-import",
                handler=self._cmd_pnl_import,
                help="Fetch latest Flex CSV (optional) and import daily P&L.",
                usage="pnl-import [csv=...] [source=flex]",
            )
        )
        self._register(
            CommandSpec(
                name="pnl-open",
                handler=self._cmd_pnl_open,
                help="Start API + web calendar and open the browser.",
                usage="pnl-open [api_port=8000] [web_port=5173]",
            )
        )
        self._register(
            CommandSpec(
                name="pnl-launch",
                handler=self._cmd_pnl_launch,
                help="Fetch + import latest Flex CSV, then open the calendar UI.",
                usage="pnl-launch [source=flex] [api_port=8000] [web_port=5173]",
            )
        )
        self._register(
            CommandSpec(
                name="set",
                handler=self._cmd_set,
                help="Set default config values for commands.",
                usage="set key=value [key=value ...]",
            )
        )
        self._register(
            CommandSpec(
                name="show",
                handler=self._cmd_show,
                help="Show config values.",
                usage="show config",
            )
        )
        self._register(
            CommandSpec(
                name="disconnect",
                handler=self._cmd_disconnect,
                help="Disconnect from IBKR.",
                usage="disconnect",
            )
        )
        self._register(
            CommandSpec(
                name="status",
                handler=self._cmd_status,
                help="Show current connection status.",
                usage="status",
            )
        )
        self._register(
            CommandSpec(
                name="quit",
                handler=self._cmd_quit,
                help="Exit the CLI.",
                usage="quit",
                aliases=("exit", "q"),
            )
        )
        self._register(
            CommandSpec(
                name="refresh",
                handler=self._cmd_refresh,
                help="Restart the CLI process to pick up code changes.",
                usage="refresh",
                aliases=("reload", "restart"),
            )
        )

    def _register(self, spec: CommandSpec) -> None:
        self._commands[spec.name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = spec.name

    def _setup_readline(self) -> None:
        if readline is None:
            return
        readline.set_completer(self._complete)
        readline.parse_and_bind("tab: complete")

    def _complete(self, text: str, state: int) -> Optional[str]:
        if readline is None:
            return None
        if state == 0:
            self._completion_matches = self._completion_matches_for(text)
        if state < len(self._completion_matches):
            return self._completion_matches[state]
        return None

    def _completion_matches_for(self, text: str) -> list[str]:
        if readline is None:
            return []
        line = readline.get_line_buffer()
        begidx = readline.get_begidx()
        if not line[:begidx].strip():
            return _match_prefix(text, self._command_names())
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.strip().split()
        if not parts:
            return _match_prefix(text, self._command_names())
        cmd_name = parts[0].lower()
        if cmd_name in {"help", "commands", "?"}:
            return _match_prefix(text, self._command_names())
        return _match_prefix(text, self._completion_candidates(cmd_name))

    def _command_names(self) -> list[str]:
        names = set(self._commands) | set(self._aliases)
        return sorted(names)

    def _completion_candidates(self, command: str) -> list[str]:
        spec = self._resolve_command(command)
        if not spec:
            return []
        name = spec.name
        if name in {"buy", "sell"}:
            return [
                "qty=",
                "limit=",
                "tif=",
                "outside_rth=",
                "account=",
                "client_tag=",
                "symbol=",
            ]
        if name == "connect":
            return [
                "paper",
                "live",
                "host=",
                "port=",
                "client_id=",
                "readonly=",
                "timeout=",
            ]
        if name == "can-trade":
            return [
                "symbol=",
                "symbols=",
                "side=",
                "qty=",
                "tif=",
                "outside_rth=",
                "exchange=",
                "currency=",
                "account=",
            ]
        if name == "breakout":
            return [
                "status",
                "list",
                "cancel",
                "ALL",
                "symbol=",
                "level=",
                "qty=",
                "tp=",
                "tp_count=",
                "tp_alloc=",
                "tp_timeout=",
                "sl=",
                "rth=",
                "bar=",
                "max_bars=",
                "tif=",
                "outside_rth=",
                "account=",
                "client_tag=",
            ]
        if name == "orders":
            return [
                "pending",
                "cancel",
                "replace",
                "broker",
                "all",
                "ALL",
                "all_clients",
                "client",
                "limit=",
                "qty=",
                "tif=",
                "outside_rth=",
                "account=",
                "scope=",
            ]
        if name == "positions":
            return ["account="]
        if name == "ingest-flex":
            return ["csv=", "account=", "source="]
        if name == "show":
            return ["config"]
        if name == "set":
            return [f"{key}=" for key in sorted(self._config)]
        return []

    def _resolve_command(self, name: str) -> Optional[CommandSpec]:
        if name in self._commands:
            return self._commands[name]
        target = self._aliases.get(name)
        if target:
            return self._commands.get(target)
        return None

    def _parse_line(self, line: str) -> tuple[Optional[str], list[str], dict[str, str]]:
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(f"Parse error: {exc}")
            return None, [], {}
        if not tokens:
            return None, [], {}
        cmd_name = tokens[0].lower()
        args, kwargs = self._parse_tokens(cmd_name, tokens[1:])
        return cmd_name, args, kwargs

    def _parse_tokens(self, command: str, tokens: list[str]) -> tuple[list[str], dict[str, str]]:
        args: list[str] = []
        kwargs: dict[str, str] = {}
        aliases = _flag_aliases(command)
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token.startswith("--"):
                key, value, consumed = _parse_long_flag(token, tokens, idx)
                if key:
                    kwargs[_normalize_key(key, aliases)] = value
                idx += consumed
                continue
            if token.startswith("-") and token != "-":
                consumed = _parse_short_flag(token, tokens, idx, aliases, kwargs)
                idx += consumed
                continue
            if "=" in token:
                key, value = token.split("=", 1)
                kwargs[_normalize_key(key, aliases)] = value
            else:
                args.append(token)
            idx += 1
        return args, kwargs

    async def _cmd_help(self, args: list[str], _kwargs: dict[str, str]) -> None:
        if args:
            name = args[0].lower()
            spec = self._resolve_command(name)
            if not spec:
                print(f"No such command: {name}")
                return
            print(f"{spec.name}: {spec.help}")
            print(f"Usage: {spec.usage}")
            return

        specs = sorted(self._commands.values(), key=lambda s: s.name)
        for spec in specs:
            print(f"{spec.name:<10} {spec.help}")

    async def _cmd_clear(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        if os.name == "nt":
            os.system("cls")
            return
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="", flush=True)
            return
        print("\n" * 100, end="")

    async def _cmd_connect(self, args: list[str], kwargs: dict[str, str]) -> None:
        mode = None
        if args:
            mode = args[0].lower()
            if mode not in {"paper", "live"}:
                print("Usage: connect [paper|live] [host=...] [port=...] [client_id=...] [readonly=true|false] [timeout=...]")
                return

        overrides: dict[str, object] = {}
        if "host" in kwargs:
            overrides["host"] = kwargs["host"]
        if "port" in kwargs:
            overrides["port"] = int(kwargs["port"])
        if "client_id" in kwargs:
            overrides["client_id"] = int(kwargs["client_id"])
        if "readonly" in kwargs:
            overrides["readonly"] = _parse_bool(kwargs["readonly"])
        if "timeout" in kwargs:
            overrides["timeout"] = float(kwargs["timeout"])

        cfg = await self._connection.connect(mode=mode, **overrides)
        if self._order_service:
            self._order_service.clear_session_phase_cache()
        self._apply_account_default(mode, cfg)
        print(
            "Connected: "
            f"{cfg.host}:{cfg.port} client_id={cfg.client_id} readonly={cfg.readonly}"
        )
        await self._seed_position_origins()
        await self._reconcile_orphan_exit_orders(trigger="connection_established")
        await self._maybe_prompt_resume_breakouts(cfg)

    def _apply_account_default(self, mode: Optional[str], cfg: object) -> None:
        if not self._account_defaults:
            return
        resolved = mode
        if resolved is None:
            port = getattr(cfg, "port", None)
            if port is not None and port == getattr(cfg, "paper_port", None):
                resolved = "paper"
            elif port is not None and port == getattr(cfg, "live_port", None):
                resolved = "live"
        if not resolved:
            return
        target = self._account_defaults.get(resolved)
        if not target:
            return
        current = (self._config.get("account") or "").strip()
        if not current or current in self._account_default_values:
            self._config["account"] = target

    async def _cmd_buy(self, args: list[str], kwargs: dict[str, str]) -> None:
        await self._submit_order(OrderSide.BUY, args, kwargs)

    async def _cmd_sell(self, args: list[str], kwargs: dict[str, str]) -> None:
        await self._submit_order(OrderSide.SELL, args, kwargs)

    async def _cmd_can_trade(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before checking symbols.")
            return

        symbols = _parse_symbol_tokens(
            args=args,
            symbol_value=kwargs.get("symbol") or kwargs.get("symbols"),
            default_symbol=_config_get(self._config, "symbol"),
        )
        if not symbols:
            print(self._commands["can-trade"].usage)
            return

        side_raw = kwargs.get("side", "BUY")
        side = side_raw.strip().upper()
        if side not in {"BUY", "SELL"}:
            print("side must be BUY or SELL")
            return

        qty_raw = kwargs.get("qty") or "1"
        try:
            qty = int(qty_raw)
        except ValueError:
            print("qty must be an integer")
            return
        if qty <= 0:
            print("qty must be greater than zero")
            return

        tif = (kwargs.get("tif") or _config_get(self._config, "tif") or "DAY").strip().upper()
        outside_rth_value = kwargs.get("outside_rth") or _config_get(self._config, "outside_rth")
        outside_rth = _parse_bool(outside_rth_value) if outside_rth_value is not None else False
        exchange = (kwargs.get("exchange") or "SMART").strip().upper()
        currency = (kwargs.get("currency") or "USD").strip().upper()
        account = kwargs.get("account") or _config_get(self._config, "account")
        timeout = max(self._connection.config.timeout, 1.0)

        account_label = account if account else "(default)"
        print(
            "Running IBKR what-if checks "
            f"(side={side} qty={qty} tif={tif} outside_rth={outside_rth} "
            f"exchange={exchange} currency={currency} account={account_label})"
        )

        for symbol in symbols:
            eligible, detail = await self._check_trade_eligibility(
                symbol=symbol,
                side=side,
                qty=qty,
                tif=tif,
                outside_rth=outside_rth,
                exchange=exchange,
                currency=currency,
                account=account,
                timeout=timeout,
            )
            label = "ELIGIBLE" if eligible else "BLOCKED"
            print(f"{symbol} {label} {detail}")

    async def _check_trade_eligibility(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        tif: str,
        outside_rth: bool,
        exchange: str,
        currency: str,
        account: Optional[str],
        timeout: float,
    ) -> tuple[bool, str]:
        ib = self._connection.ib
        contract = Stock(symbol, exchange, currency)
        try:
            contracts = await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=timeout)
        except asyncio.TimeoutError:
            return False, f"contract qualification timed out after {timeout:.1f}s"
        except Exception as exc:
            return False, f"contract qualification failed: {exc}"
        if not contracts:
            return False, "contract qualification returned no match"

        order = MarketOrder(side, qty, tif=tif)
        order.whatIf = True
        order.outsideRth = outside_rth
        if account:
            order.account = account

        expected_req_id: Optional[int] = None
        captured_gateway_messages: list[
            tuple[Optional[int], Optional[int], Optional[str], Optional[str]]
        ] = []
        unsubscribe_gateway: Optional[Callable[[], None]] = None
        subscribe_gateway = getattr(self._connection, "subscribe_gateway_messages", None)
        if callable(subscribe_gateway):
            def _capture_gateway_message(
                req_id: Optional[int],
                code: Optional[int],
                message: Optional[str],
                advanced: Optional[str],
            ) -> None:
                if req_id is not None and req_id < 0:
                    return
                captured_gateway_messages.append((req_id, code, message, advanced))

            unsubscribe_gateway = subscribe_gateway(_capture_gateway_message)

        try:
            state_awaitable: Awaitable[object]
            client = getattr(ib, "client", None)
            wrapper = getattr(ib, "wrapper", None)
            get_req_id = getattr(client, "getReqId", None) if client is not None else None
            start_req = getattr(wrapper, "startReq", None) if wrapper is not None else None
            place_order = getattr(client, "placeOrder", None) if client is not None else None
            if callable(get_req_id) and callable(start_req) and callable(place_order):
                expected_req_id = int(get_req_id())
                suppress_gateway_req_id(expected_req_id)
                what_if_order = copy.copy(order)
                what_if_order.whatIf = True
                state_awaitable = start_req(expected_req_id, contracts[0])
                place_order(expected_req_id, contracts[0], what_if_order)
            else:
                state_awaitable = ib.whatIfOrderAsync(contracts[0], order)
            state = await asyncio.wait_for(state_awaitable, timeout=timeout)
        except asyncio.TimeoutError:
            return False, f"what-if timed out after {timeout:.1f}s"
        except Exception as exc:
            return False, f"what-if rejected: {exc}"
        finally:
            if unsubscribe_gateway is not None:
                unsubscribe_gateway()

        status = str(getattr(state, "status", "") or "").strip()
        warning = str(getattr(state, "warningText", "") or "").strip()
        if warning and _is_trade_blocking_warning(warning):
            return False, warning

        normalized_status = status.lower()
        if normalized_status in {"inactive", "cancelled", "apicancelled", "rejected"}:
            if warning:
                return False, f"{status}: {warning}"
            return False, f"what-if status={status}"
        gateway_reject = _find_trade_blocking_gateway_reject(
            captured_gateway_messages,
            expected_req_id=expected_req_id,
        )
        if gateway_reject:
            return False, gateway_reject
        if warning:
            return True, f"warning: {warning}"
        if status:
            return True, f"what-if status={status}"
        return True, "what-if accepted"

    async def _cmd_breakout(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._bar_stream or not self._order_service:
            print("Breakout not configured.")
            return
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before starting a breakout watcher.")
            return
        if args:
            action = args[0].lower()
            if action in {"status", "list"}:
                self._print_breakout_status()
                return
            if action in {"cancel", "stop"}:
                cancel_tokens = list(args[1:])
                symbol_kw = kwargs.get("symbol")
                if symbol_kw:
                    cancel_tokens.extend(symbol_kw.split(","))
                if not cancel_tokens:
                    await self._stop_breakouts(symbol=None)
                    return

                symbols: list[str] = []
                seen: set[str] = set()
                for token in cancel_tokens:
                    for raw_symbol in token.split(","):
                        symbol = raw_symbol.strip().upper()
                        if not symbol:
                            continue
                        if symbol == "ALL":
                            await self._stop_breakouts(symbol=None)
                            return
                        if symbol in seen:
                            continue
                        seen.add(symbol)
                        symbols.append(symbol)
                if not symbols:
                    await self._stop_breakouts(symbol=None)
                    return
                for symbol in symbols:
                    await self._stop_breakouts(symbol=symbol)
                return

        symbol = args[0] if args else kwargs.get("symbol") or _config_get(self._config, "symbol")
        if not symbol:
            print(self._commands["breakout"].usage)
            return
        positional_level = args[1] if len(args) > 1 else None
        positional_qty = args[2] if len(args) > 2 else None
        positional_tp = None
        positional_sl = None
        auto_tp_count_from_token = None
        trailing = args[3:] if len(args) > 3 else []
        if trailing:
            maybe_auto = _parse_tp_mode_token(trailing[0])
            if maybe_auto is not None:
                auto_tp_count_from_token = maybe_auto
                positional_sl = trailing[1] if len(trailing) > 1 else None
                if len(trailing) > 2:
                    print(self._commands["breakout"].usage)
                    return
            else:
                positional_tp = trailing[0]
                positional_sl = trailing[1] if len(trailing) > 1 else None
                if len(trailing) > 2:
                    print(self._commands["breakout"].usage)
                    return

        level_raw = kwargs.get("level") or positional_level or _config_get(self._config, "level")
        qty_raw = kwargs.get("qty") or positional_qty or _config_get(self._config, "qty")
        if level_raw is None or qty_raw is None:
            print(self._commands["breakout"].usage)
            return
        try:
            level = float(level_raw)
        except ValueError:
            print("level must be a number")
            return
        if level <= 0:
            print("level must be greater than zero")
            return
        try:
            qty = int(qty_raw)
        except ValueError:
            print("qty must be an integer")
            return
        if qty <= 0:
            print("qty must be greater than zero")
            return

        tp_raw = kwargs.get("tp") or positional_tp or _config_get(self._config, "tp")
        sl_raw = kwargs.get("sl") or positional_sl or _config_get(self._config, "sl")
        tp_count_kw = kwargs.get("tp_count")
        tp_alloc_kw = kwargs.get("tp_alloc")
        tp_exec_kw = kwargs.get("tp_exec") or kwargs.get("ladder_exec")
        tp_count_raw = tp_count_kw or _config_get(self._config, "tp_count")
        tp_alloc_raw = tp_alloc_kw or _config_get(self._config, "tp_alloc")
        tp_timeout_raw = kwargs.get("tp_timeout") or _config_get(self._config, "tp_timeout")
        tp_exec_raw = (
            tp_exec_kw
            or _config_get(self._config, "tp_exec")
            or _config_get(self._config, "ladder_exec")
        )
        tp_exec_explicit = tp_exec_kw is not None and str(tp_exec_kw).strip() != ""

        auto_tp_count_from_value = (
            _parse_tp_mode_token(str(tp_raw))
            if tp_raw is not None
            else None
        )
        if auto_tp_count_from_token is None and auto_tp_count_from_value is not None:
            auto_tp_count_from_token = auto_tp_count_from_value

        if auto_tp_count_from_token is not None:
            tp_raw = "auto"
            # Shorthand token must override persisted defaults; only fail on
            # explicit same-command tp_count conflicts.
            if tp_count_kw is not None:
                parsed = _coerce_int(tp_count_kw)
                if parsed is None or parsed != auto_tp_count_from_token:
                    print("tp_count must match tp-<n> when both are provided")
                    return
            tp_count_raw = str(auto_tp_count_from_token)
            if tp_alloc_kw is None:
                tp_alloc_raw = None
            if not tp_exec_explicit:
                tp_exec_raw = None

        try:
            ladder_execution_mode = _parse_ladder_execution_mode(tp_exec_raw)
        except ValueError:
            print("tp_exec must be 'attached', 'detached', or 'detached70'")
            return

        take_profit = None
        take_profits = None
        take_profit_qtys = None
        stop_loss = None
        tp_reprice_on_fill = False
        tp_reprice_bar_size = "1 min"
        tp_reprice_use_rth = False
        tp_reprice_timeout_seconds = 5.0
        if tp_raw is not None or sl_raw is not None:
            if tp_raw is None or sl_raw is None:
                print("tp and sl must be provided together")
                return
            try:
                stop_loss = float(sl_raw)
            except ValueError:
                print("sl must be a number")
                return
            if stop_loss <= 0:
                print("sl must be greater than zero")
                return

            tp_text = str(tp_raw).strip()
            tp_text_lower = tp_text.lower()
            if tp_text_lower == "auto":
                if not self._tp_service:
                    print("TP service not configured.")
                    return
                tp_count = _coerce_int(tp_count_raw) if tp_count_raw is not None else 3
                if tp_count not in {1, 2, 3}:
                    print("tp_count must be 1, 2, or 3 when tp=auto")
                    return
            elif "-" in tp_text:
                parts = [part.strip() for part in tp_text.split("-") if part.strip()]
                if len(parts) < 2:
                    print("tp must include at least two levels when using a ladder")
                    return
                try:
                    take_profits = [float(part) for part in parts]
                except ValueError:
                    print("tp must be a list of numbers like 1.1-1.3-1.5")
                    return
            else:
                try:
                    take_profit = float(tp_text)
                except ValueError:
                    print("tp must be a number")
                    return

        bar_size = (
            kwargs.get("bar")
            or kwargs.get("bar_size")
            or _config_get(self._config, "bar_size")
            or "1 min"
        )
        fast_value = kwargs.get("fast") or _config_get(self._config, "fast")
        fast_enabled = _parse_bool(fast_value) if fast_value is not None else True
        fast_bar_size = (
            kwargs.get("fast_bar")
            or _config_get(self._config, "fast_bar")
            or "1 secs"
        )
        use_rth_value = kwargs.get("rth") or kwargs.get("use_rth") or _config_get(self._config, "use_rth")
        use_rth = _parse_bool(use_rth_value) if use_rth_value is not None else False
        outside_rth_value = kwargs.get("outside_rth") or _config_get(self._config, "outside_rth")
        if outside_rth_value is None:
            outside_rth = not use_rth
        else:
            outside_rth = _parse_bool(outside_rth_value)
        tif = kwargs.get("tif") or _config_get(self._config, "tif") or "DAY"
        account = kwargs.get("account") or _config_get(self._config, "account")
        entry_raw = (
            kwargs.get("entry")
            or kwargs.get("entry_type")
            or _config_get(self._config, "entry")
        )
        entry_type = OrderType.LIMIT
        if entry_raw is not None:
            try:
                entry_type = _parse_entry_type(entry_raw)
            except ValueError:
                print("entry must be 'limit' (lmt) or 'market' (mkt)")
                return
        if entry_type == OrderType.LIMIT and not self._quote_port and not self._quote_stream:
            print("limit entry requires quote_port or quote_stream to be configured")
            return

        max_bars_raw = kwargs.get("max_bars") or _config_get(self._config, "max_bars")
        max_bars = None
        if max_bars_raw is not None:
            try:
                max_bars = int(max_bars_raw)
            except ValueError:
                print("max_bars must be an integer")
                return

        quote_age_raw = (
            kwargs.get("quote_age")
            or kwargs.get("quote_max_age")
            or _config_get(self._config, "quote_age")
            or _config_get(self._config, "quote_max_age")
        )
        quote_max_age_seconds = 2.0
        if quote_age_raw is not None:
            try:
                quote_max_age_seconds = float(quote_age_raw)
            except ValueError:
                print("quote_age must be a number (seconds)")
                return

        symbol = symbol.strip().upper()
        if not symbol:
            print(self._commands["breakout"].usage)
            return

        if tp_raw is not None or sl_raw is not None:
            tp_reprice_bar_size = kwargs.get("tp_bar") or kwargs.get("tp_bar_size") or bar_size
            tp_reprice_use_rth_value = kwargs.get("tp_rth") or kwargs.get("tp_use_rth")
            tp_reprice_use_rth = (
                _parse_bool(tp_reprice_use_rth_value)
                if tp_reprice_use_rth_value is not None
                else use_rth
            )
            if tp_timeout_raw is not None:
                try:
                    tp_reprice_timeout_seconds = float(tp_timeout_raw)
                except ValueError:
                    print("tp_timeout must be a number (seconds)")
                    return
            if tp_reprice_timeout_seconds <= 0:
                print("tp_timeout must be greater than zero")
                return

            if str(tp_raw).strip().lower() == "auto":
                tp_count = _coerce_int(tp_count_raw) if tp_count_raw is not None else 3
                if tp_count is None or tp_count not in {1, 2, 3}:
                    print("tp_count must be 1, 2, or 3 when tp=auto")
                    return
                try:
                    result = await self._tp_service.compute_levels(
                        TakeProfitRequest(
                            symbol=symbol,
                            bar_size=tp_reprice_bar_size,
                            use_rth=tp_reprice_use_rth,
                            anchor_price=level,
                        )
                    )
                except RuntimeError as exc:
                    print(f"TP calculation failed: {exc}")
                    return
                if len(result.levels) < tp_count:
                    print(f"TP calculation returned only {len(result.levels)} level(s); need {tp_count}.")
                    return
                resolved_levels = [level_obj.price for level_obj in result.levels[:tp_count]]
                if not _validate_take_profit_levels(resolved_levels):
                    print("auto TP levels are not strictly increasing positive prices")
                    return
                if resolved_levels[0] <= stop_loss:
                    print("auto TP levels must be above stop loss")
                    return
                if tp_count == 1:
                    take_profit = resolved_levels[0]
                    take_profits = None
                else:
                    take_profits = resolved_levels
                    take_profit = None
                    ratios = _default_take_profit_ratios(tp_count)
                    if tp_alloc_raw is not None:
                        ratios = _parse_take_profit_ratios(tp_alloc_raw, tp_count)
                        if ratios is None:
                            print("tp_alloc must match tp_count, e.g. 80-20 or 60-30-10")
                            return
                    try:
                        take_profit_qtys = _split_qty_by_ratios(qty, ratios)
                    except ValueError as exc:
                        print(f"tp_alloc invalid: {exc}")
                        return
                    tp_reprice_on_fill = True
            else:
                if take_profit is not None:
                    if take_profit <= 0:
                        print("tp must be greater than zero")
                        return
                    if take_profit <= stop_loss:
                        print("tp must be above stop loss")
                        return
                if take_profits:
                    if len(take_profits) not in {2, 3}:
                        print("tp ladder must include 2 or 3 levels")
                        return
                    if not _validate_take_profit_levels(take_profits):
                        print("tp ladder levels must be strictly increasing and greater than zero")
                        return
                    if take_profits[0] <= stop_loss:
                        print("tp ladder levels must be above stop loss")
                        return
                    if tp_alloc_raw is not None:
                        ratios = _parse_take_profit_ratios(tp_alloc_raw, len(take_profits))
                        if ratios is None:
                            print("tp_alloc must match ladder size, e.g. 80-20 or 60-30-10")
                            return
                        try:
                            take_profit_qtys = _split_qty_by_ratios(qty, ratios)
                        except ValueError as exc:
                            print(f"tp_alloc invalid: {exc}")
                            return

        if not tp_exec_explicit and take_profits:
            if len(take_profits) == 2:
                ladder_execution_mode = LadderExecutionMode.DETACHED_70_30
            elif len(take_profits) == 3:
                ladder_execution_mode = LadderExecutionMode.DETACHED

        if ladder_execution_mode == LadderExecutionMode.ATTACHED and take_profits:
            print("tp_exec=attached supports only single tp/sl (use tp=LEVEL, not a ladder).")
            return

        if ladder_execution_mode == LadderExecutionMode.DETACHED:
            if not take_profits or len(take_profits) != 3:
                print("tp_exec=detached requires a 3-level tp ladder with sl.")
                return
            if tp_reprice_on_fill:
                tp_reprice_on_fill = False
                print("tp_reprice disabled for tp_exec=detached")

        if ladder_execution_mode == LadderExecutionMode.DETACHED_70_30:
            if not take_profits or len(take_profits) != 2:
                print("tp_exec=detached70 requires a 2-level tp ladder with sl (tp=LEVEL1-LEVEL2).")
                return
            if tp_alloc_raw is not None:
                ratios = _parse_take_profit_ratios(tp_alloc_raw, 2)
                if ratios is None:
                    print("tp_alloc must be 70-30 when tp_exec=detached70")
                    return
                if abs(ratios[0] - 0.7) > 1e-9 or abs(ratios[1] - 0.3) > 1e-9:
                    print("tp_alloc must be exactly 70-30 when tp_exec=detached70")
                    return
            try:
                take_profit_qtys = _split_qty_by_ratios(qty, [0.7, 0.3])
            except ValueError as exc:
                print(f"tp_exec detached70 invalid: {exc}")
                return
            if tp_reprice_on_fill:
                tp_reprice_on_fill = False
                print("tp_reprice disabled for tp_exec=detached70")

        client_tag = kwargs.get("client_tag") or _config_get(self._config, "client_tag")
        if not client_tag:
            client_tag = _default_breakout_client_tag(symbol, level)

        run_config = BreakoutRunConfig(
            symbol=symbol,
            qty=qty,
            rule=BreakoutRuleConfig(level=level, fast_entry=FastEntryConfig(enabled=fast_enabled)),
            entry_type=entry_type,
            take_profit=take_profit,
            take_profits=take_profits,
            take_profit_qtys=take_profit_qtys,
            stop_loss=stop_loss,
            use_rth=use_rth,
            bar_size=bar_size,
            fast_bar_size=fast_bar_size,
            max_bars=max_bars,
            tif=tif,
            outside_rth=outside_rth,
            account=account,
            client_tag=client_tag,
            quote_max_age_seconds=quote_max_age_seconds,
            tp_reprice_on_fill=tp_reprice_on_fill,
            tp_reprice_bar_size=tp_reprice_bar_size,
            tp_reprice_use_rth=tp_reprice_use_rth,
            tp_reprice_timeout_seconds=tp_reprice_timeout_seconds,
            ladder_execution_mode=ladder_execution_mode,
        )

        self._launch_breakout(run_config, source="user")

    async def _cmd_tp(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._tp_service:
            print("TP service not configured.")
            return
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before requesting TP levels.")
            return
        if not args:
            print(self._commands["tp"].usage)
            return

        symbol = args[0].strip().upper()
        if not symbol:
            print(self._commands["tp"].usage)
            return

        bar_size = kwargs.get("bar") or kwargs.get("bar_size") or "1 min"
        use_rth_value = kwargs.get("rth") or kwargs.get("use_rth")
        use_rth = _parse_bool(use_rth_value) if use_rth_value is not None else False

        try:
            result = await self._tp_service.compute_levels(
                TakeProfitRequest(
                    symbol=symbol,
                    bar_size=bar_size,
                    use_rth=use_rth,
                )
            )
        except NotImplementedError:
            print("TP calculation is not implemented yet.")
            return
        except RuntimeError as exc:
            print(f"TP calculation failed: {exc}")
            return

        if not result.levels:
            print("No TP levels found.")
            return

        lookback = f"{result.lookback_days}d" if result.lookback_days is not None else "n/a"
        suffix = " (fallback)" if result.used_fallback else ""
        print(f"{symbol} TP levels (lookback={lookback}, bar={bar_size}){suffix}:")
        for idx, level in enumerate(result.levels, start=1):
            print(f"  TP{idx}: {level.price:g} ({level.reason.value})")

    def _breakout_task_name(self, config: BreakoutRunConfig) -> str:
        return f"breakout:{config.symbol}:{config.rule.level:g}"

    def _launch_breakout(self, config: BreakoutRunConfig, *, source: str) -> bool:
        if not self._bar_stream or not self._order_service:
            print("Breakout not configured.")
            return False
        task_name = self._breakout_task_name(config)
        if task_name in self._breakout_tasks:
            print(f"Breakout watcher already running: {task_name}")
            return False
        task = asyncio.create_task(
            run_breakout(
                config,
                bar_stream=self._bar_stream,
                order_service=self._order_service,
                quote_port=self._quote_port,
                quote_stream=self._quote_stream,
                event_bus=self._event_bus,
            ),
            name=task_name,
        )
        self._breakout_tasks[task_name] = (config, task)
        self._schedule_session_phase_prewarm(config)
        self._register_tp_reprice_session(config)
        task.add_done_callback(lambda t: self._on_breakout_done(task_name, t))
        if source == "resume":
            print(f"Resumed breakout watcher: {task_name}")
        else:
            print(f"Breakout watcher started: {task_name}")
        self._persist_breakout_state()
        return True

    def _schedule_session_phase_prewarm(self, config: BreakoutRunConfig) -> None:
        if not self._order_service:
            return
        symbol = config.symbol.strip().upper()
        if not symbol:
            return
        existing = self._session_phase_prewarm_tasks.get(symbol)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._order_service.prewarm_session_phase(symbol=symbol),
            name=f"session-phase-prewarm:{symbol}",
        )
        self._session_phase_prewarm_tasks[symbol] = task
        task.add_done_callback(lambda done_task, sym=symbol: self._on_session_phase_prewarm_done(sym, done_task))

    def _on_session_phase_prewarm_done(self, symbol: str, task: asyncio.Task) -> None:
        current = self._session_phase_prewarm_tasks.get(symbol)
        if current is task:
            self._session_phase_prewarm_tasks.pop(symbol, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _print_exception(f"Session phase prewarm failed: {symbol}", exc)

    def _register_tp_reprice_session(self, config: BreakoutRunConfig) -> None:
        if not config.tp_reprice_on_fill:
            return
        if config.ladder_execution_mode != LadderExecutionMode.ATTACHED:
            return
        if not config.client_tag or not config.take_profits or config.stop_loss is None:
            return
        if len(config.take_profits) not in {2, 3}:
            return
        self._drop_tp_reprice_session(config.client_tag)
        self._tp_reprice_sessions[config.client_tag] = _BreakoutTpRepriceSession(
            client_tag=config.client_tag,
            symbol=config.symbol,
            qty=config.qty,
            stop_loss=config.stop_loss,
            tp_count=len(config.take_profits),
            bar_size=config.tp_reprice_bar_size,
            use_rth=config.tp_reprice_use_rth,
            timeout_seconds=config.tp_reprice_timeout_seconds,
            account=config.account,
            take_profit_qtys=list(config.take_profit_qtys) if config.take_profit_qtys else None,
        )

    def _drop_tp_reprice_session(self, client_tag: str) -> None:
        self._tp_reprice_sessions.pop(client_tag, None)
        task = self._tp_reprice_tasks.pop(client_tag, None)
        if task and not task.done():
            task.cancel()

    def _update_running_breakout_take_profits(self, client_tag: str, take_profits: list[float]) -> None:
        if not take_profits:
            return
        for task_name, (config, task) in list(self._breakout_tasks.items()):
            if config.client_tag != client_tag:
                continue
            updated = replace(
                config,
                take_profit=take_profits[0] if len(take_profits) == 1 else None,
                take_profits=list(take_profits) if len(take_profits) > 1 else None,
            )
            self._breakout_tasks[task_name] = (updated, task)

    async def _stop_tp_reprice_tasks(self) -> None:
        tasks = list(self._tp_reprice_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tp_reprice_tasks.clear()
        self._tp_reprice_sessions.clear()

    def _handle_tp_reprice_event(self, event: object) -> None:
        if not self._tp_reprice_sessions:
            return
        if isinstance(event, BreakoutStopped):
            if event.client_tag and event.reason not in {"order_submitted", "order_submitted_fast"}:
                self._drop_tp_reprice_session(event.client_tag)
            return
        if isinstance(event, OrderIdAssigned):
            client_tag = event.spec.client_tag
            if not client_tag:
                return
            session = self._tp_reprice_sessions.get(client_tag)
            if not session:
                return
            if event.order_id is not None and session.parent_order_id is None:
                session.parent_order_id = event.order_id
            return
        if isinstance(event, OrderStatusChanged):
            client_tag = event.spec.client_tag
            if not client_tag:
                return
            session = self._tp_reprice_sessions.get(client_tag)
            if not session:
                return
            status = str(event.status or "").strip().lower()
            if status in {"cancelled", "inactive", "api cancelled", "apicancelled"} and not session.reprice_started:
                self._drop_tp_reprice_session(client_tag)
            return
        if isinstance(event, BracketChildOrderStatusChanged):
            client_tag = event.client_tag
            if not client_tag:
                return
            session = self._tp_reprice_sessions.get(client_tag)
            if not session:
                return
            tp_index = _tp_index_from_kind(event.kind)
            if tp_index is None:
                return
            if event.order_id is not None:
                session.tp_child_order_ids[tp_index] = event.order_id
            return
        if isinstance(event, BracketChildOrderFilled):
            client_tag = event.client_tag
            if not client_tag:
                return
            session = self._tp_reprice_sessions.get(client_tag)
            if not session:
                return
            if event.kind == "stop_loss":
                self._drop_tp_reprice_session(client_tag)
                return
            tp_index = _tp_index_from_kind(event.kind)
            if tp_index is None:
                return
            if event.order_id is not None:
                session.tp_child_order_ids[tp_index] = event.order_id
            session.tp_filled = True
            if not session.reprice_started:
                self._drop_tp_reprice_session(client_tag)
            return
        if isinstance(event, OrderFilled):
            client_tag = event.spec.client_tag
            if not client_tag:
                return
            session = self._tp_reprice_sessions.get(client_tag)
            if not session:
                return
            if session.reprice_started:
                return
            if session.parent_order_id is not None and event.order_id != session.parent_order_id:
                return
            if not _is_order_fully_filled(
                status=event.status,
                filled_qty=event.filled_qty,
                remaining_qty=event.remaining_qty,
                expected_qty=session.qty,
            ):
                return
            fill_price = _coalesce_number(event.avg_fill_price, event.spec.limit_price)
            if fill_price is None or fill_price <= 0:
                return
            if session.parent_order_id is None and event.order_id is not None:
                session.parent_order_id = event.order_id
            session.reprice_started = True
            task = asyncio.create_task(
                self._run_tp_reprice_session(client_tag, fill_price),
                name=f"tp-reprice:{client_tag}",
            )
            self._tp_reprice_tasks[client_tag] = task
            task.add_done_callback(lambda task_obj, tag=client_tag: self._on_tp_reprice_done(tag, task_obj))
            return

    async def _run_tp_reprice_session(self, client_tag: str, fill_price: float) -> None:
        if not self._tp_service or not self._order_service:
            return
        session = self._tp_reprice_sessions.get(client_tag)
        if not session:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + session.timeout_seconds
        while True:
            current = self._tp_reprice_sessions.get(client_tag)
            if current is None:
                return
            if current.tp_filled:
                print(f"TP reprice skipped: {current.symbol} (tp already filled)")
                return
            if len(current.tp_child_order_ids) >= current.tp_count:
                session = current
                break
            if loop.time() >= deadline:
                print(f"TP reprice skipped: {current.symbol} (tp child ids not ready)")
                return
            await asyncio.sleep(0.1)

        try:
            result = await self._tp_service.compute_levels(
                TakeProfitRequest(
                    symbol=session.symbol,
                    bar_size=session.bar_size,
                    use_rth=session.use_rth,
                    anchor_price=fill_price,
                )
            )
        except Exception as exc:
            _print_exception("TP reprice failed", exc)
            return
        if len(result.levels) < session.tp_count:
            print(
                f"TP reprice skipped: {session.symbol} "
                f"(got {len(result.levels)} levels, need {session.tp_count})"
            )
            return
        new_levels = [level.price for level in result.levels[: session.tp_count]]
        if not _validate_take_profit_levels(new_levels):
            print(f"TP reprice skipped: {session.symbol} (invalid level ordering)")
            return
        if new_levels[0] <= session.stop_loss:
            print(f"TP reprice skipped: {session.symbol} (levels below stop)")
            return

        current = self._tp_reprice_sessions.get(client_tag)
        if current is None:
            return
        if current.tp_filled:
            print(f"TP reprice skipped: {current.symbol} (tp already filled)")
            return

        applied_indexes: list[int] = []
        for idx, level_price in enumerate(new_levels, start=1):
            current = self._tp_reprice_sessions.get(client_tag)
            if current is None:
                return
            if current.tp_filled:
                print(f"TP reprice skipped: {current.symbol} (tp already filled)")
                if applied_indexes:
                    applied_text = ",".join(str(item) for item in applied_indexes)
                    print(
                        f"TP reprice partial: {current.symbol} "
                        f"(applied=[{applied_text}] before fill; no tp state update event)"
                    )
                return
            order_id = current.tp_child_order_ids.get(idx)
            if order_id is None:
                print(f"TP reprice skipped: {current.symbol} (missing tp order id {idx})")
                if applied_indexes:
                    applied_text = ",".join(str(item) for item in applied_indexes)
                    print(
                        f"TP reprice partial: {current.symbol} "
                        f"(applied=[{applied_text}] before missing id; no tp state update event)"
                    )
                return
            ack = await self._order_service.replace_order(
                OrderReplaceSpec(order_id=order_id, limit_price=level_price)
            )
            if not _is_replace_ack_accepted(ack, expected_order_id=order_id):
                status_text = str(getattr(ack, "status", None))
                print(
                    f"TP reprice skipped: {current.symbol} "
                    f"(replace not accepted idx={idx} order_id={order_id} status={status_text})"
                )
                if applied_indexes:
                    applied_text = ",".join(str(item) for item in applied_indexes)
                    print(
                        f"TP reprice partial: {current.symbol} "
                        f"(applied=[{applied_text}] before rejection; no tp state update event)"
                    )
                return
            applied_indexes.append(idx)
            current = self._tp_reprice_sessions.get(client_tag)
            if current is None:
                return
            if current.tp_filled:
                print(f"TP reprice skipped: {current.symbol} (tp already filled)")
                applied_text = ",".join(str(item) for item in applied_indexes)
                print(
                    f"TP reprice partial: {current.symbol} "
                    f"(applied=[{applied_text}] before fill; no tp state update event)"
                )
                return

        if len(applied_indexes) != session.tp_count:
            print(
                f"TP reprice skipped: {session.symbol} "
                f"(applied {len(applied_indexes)}/{session.tp_count}; no tp state update event)"
            )
            return

        self._update_running_breakout_take_profits(client_tag, new_levels)
        if self._event_bus:
            self._event_bus.publish(
                BreakoutTakeProfitsUpdated.now(
                    symbol=session.symbol,
                    take_profits=new_levels,
                    take_profit_qtys=session.take_profit_qtys,
                    stop_loss=session.stop_loss,
                    account=session.account,
                    client_tag=session.client_tag,
                    source="fill_reprice",
                )
            )

        levels_text = ",".join(f"{level:g}" for level in new_levels)
        print(f"TP reprice applied: {session.symbol} fill={fill_price:g} tp=[{levels_text}]")

    def _on_tp_reprice_done(self, client_tag: str, task: asyncio.Task) -> None:
        self._tp_reprice_tasks.pop(client_tag, None)
        self._tp_reprice_sessions.pop(client_tag, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _print_exception("TP reprice task failed", exc)

    async def _maybe_prompt_resume_breakouts(self, cfg: object) -> None:
        if self._breakout_tasks:
            return
        if not self._bar_stream or not self._order_service:
            print("Breakout not configured; cannot resume stored breakouts.")
            return
        configs = self._load_breakout_state()
        if not configs:
            return
        print("Stored breakout watchers found:")
        for config in configs:
            print(f"  - {self._format_breakout_summary(config)}")
        if not self._is_paper_connection(cfg):
            print("Resume is disabled for live connections (PAPER_ONLY guard).")
            if await self._prompt_yes_no("Clear stored breakouts? (y/N): "):
                self._clear_breakout_state()
                print("Cleared stored breakouts.")
            return
        if not await self._prompt_yes_no("Resume previous breakouts? (y/N): "):
            self._clear_breakout_state()
            print("Cleared stored breakouts.")
            return
        resumed = 0
        for config in configs:
            if self._launch_breakout(config, source="resume"):
                resumed += 1
        if resumed == 0:
            print("No breakout watchers resumed.")
            self._clear_breakout_state()

    async def _prompt_yes_no(self, prompt: str) -> bool:
        try:
            response = await asyncio.to_thread(input, prompt)
        except asyncio.CancelledError:
            self._clear_cancel_state()
            return False
        except (EOFError, KeyboardInterrupt):
            return False
        if not response:
            return False
        return _parse_bool(response)

    def _is_paper_connection(self, cfg: object) -> bool:
        port = getattr(cfg, "port", None)
        paper_port = getattr(cfg, "paper_port", None)
        if port is None or paper_port is None:
            return False
        return port == paper_port

    async def _cmd_orders(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        if _args:
            subcommand = _args[0].lower()
            if subcommand == "cancel":
                await self._cmd_order_cancel(_args[1:], _kwargs)
                return
            if subcommand == "replace":
                await self._cmd_order_replace(_args[1:], _kwargs)
                return
            if subcommand == "broker":
                await self._cmd_order_broker(_args[1:], _kwargs)
                return
        if not self._order_tracker:
            print("Order tracker not configured.")
            return
        pending_only = _is_pending_only(_args, _kwargs)
        lines = self._order_tracker.format_table(pending_only=pending_only)
        if not lines:
            print("No orders recorded.")
            return
        for line in lines:
            print(line)

    async def _cmd_order_cancel(self, args: list[str], _kwargs: dict[str, str]) -> None:
        if not self._order_service:
            print("Order service not configured.")
            return
        if not args:
            print("Usage: orders cancel ORDER_ID")
            return
        order_id = _coerce_int(args[0])
        if not order_id or order_id <= 0:
            print("order_id must be a positive integer")
            return
        spec = OrderCancelSpec(order_id=order_id)
        try:
            ack = await self._order_service.cancel_order(spec)
        except OrderValidationError as exc:
            print(f"Cancel rejected: {exc}")
            return
        except Exception as exc:
            _print_exception("Cancel failed", exc)
            return
        print(f"Cancel requested: order_id={ack.order_id} status={ack.status}")

    async def _cmd_order_replace(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._order_service:
            print("Order service not configured.")
            return
        if not self._order_tracker:
            print("Order tracker not configured.")
            return
        if not args:
            print(
                "Usage: orders replace ORDER_ID [limit=...] [qty=...] [tif=DAY] [outside_rth=true|false]"
            )
            return
        order_id = _coerce_int(args[0])
        if not order_id or order_id <= 0:
            print("order_id must be a positive integer")
            return
        record = self._order_tracker.get_order_record(order_id)
        if not record:
            print("Order id not tracked; replace only supports orders placed by this session.")
            return
        if record.spec.order_type != OrderType.LIMIT:
            print("Replace only supports limit orders.")
            return
        qty = _coerce_int(kwargs.get("qty")) if "qty" in kwargs else None
        if "qty" in kwargs and qty is None:
            print("qty must be an integer")
            return
        limit_price = _coerce_float(kwargs.get("limit")) if "limit" in kwargs else None
        if "limit" in kwargs and limit_price is None:
            print("limit must be a number")
            return
        tif = _coerce_str(kwargs.get("tif")) if "tif" in kwargs else None
        if "tif" in kwargs and tif is None:
            print("tif must be a non-empty string")
            return
        outside_rth = None
        if "outside_rth" in kwargs:
            outside_rth = _coerce_bool(kwargs.get("outside_rth"), default=False)
        spec = OrderReplaceSpec(
            order_id=order_id,
            qty=qty,
            limit_price=limit_price,
            tif=tif,
            outside_rth=outside_rth,
        )
        try:
            ack = await self._order_service.replace_order(spec)
        except OrderValidationError as exc:
            print(f"Replace rejected: {exc}")
            return
        except Exception as exc:
            _print_exception("Replace failed", exc)
            return
        print(f"Replace requested: order_id={ack.order_id} status={ack.status}")

    async def _cmd_order_broker(self, args: list[str], kwargs: dict[str, str]) -> None:
        if args and args[0].strip().lower() == "cancel":
            cancel_args = list(args[1:])
            if cancel_args and cancel_args[0].strip().lower() == "all":
                await self._cmd_order_broker_cancel_all(cancel_args[1:], kwargs)
                return
            await self._cmd_order_cancel(cancel_args, kwargs)
            return

        if not self._active_orders_service:
            print("Active orders service not configured.")
            return
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before requesting broker orders.")
            return

        normalized_args = list(args)
        scope_arg = None
        if normalized_args:
            parsed_scope = normalized_args[0].strip().lower().replace("-", "_")
            if parsed_scope in {"client", "all_clients"}:
                scope_arg = parsed_scope
                normalized_args = normalized_args[1:]
        if normalized_args:
            print("Usage: orders broker [account=...] [scope=client|all_clients]")
            return

        account = _coerce_str(kwargs.get("account"))
        scope_raw = _coerce_str(kwargs.get("scope"))
        scope = (scope_raw or scope_arg or "client").strip().lower().replace("-", "_")
        if scope not in {"client", "all_clients"}:
            print("scope must be 'client' or 'all_clients'")
            return

        try:
            snapshots = await self._active_orders_service.list_active_orders(
                account=account,
                scope=scope,
            )
        except ValueError as exc:
            print(f"Broker orders rejected: {exc}")
            return
        except Exception as exc:
            _print_exception("Broker orders failed", exc)
            return

        if not snapshots:
            print("No active broker orders found.")
            return

        print(f"Active broker orders (scope={scope})")
        for line in _format_active_orders_table(snapshots):
            print(line)

    async def _cmd_order_broker_cancel_all(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._order_service:
            print("Order service not configured.")
            return
        if not self._active_orders_service:
            print("Active orders service not configured.")
            return
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before requesting broker orders.")
            return

        normalized_args = list(args)
        scope_arg = None
        if normalized_args:
            parsed_scope = normalized_args[0].strip().lower().replace("-", "_")
            if parsed_scope in {"client", "all_clients"}:
                scope_arg = parsed_scope
                normalized_args = normalized_args[1:]
        if normalized_args:
            print("Usage: orders broker cancel all [account=...] [scope=client|all_clients]")
            return

        account = _coerce_str(kwargs.get("account"))
        scope_raw = _coerce_str(kwargs.get("scope"))
        scope = (scope_raw or scope_arg or "client").strip().lower().replace("-", "_")
        if scope not in {"client", "all_clients"}:
            print("scope must be 'client' or 'all_clients'")
            return

        try:
            snapshots = await self._active_orders_service.list_active_orders(
                account=account,
                scope=scope,
            )
        except ValueError as exc:
            print(f"Broker cancel-all rejected: {exc}")
            return
        except Exception as exc:
            _print_exception("Broker cancel-all failed", exc)
            return

        if not snapshots:
            print("No active broker orders found.")
            return

        order_ids: list[int] = []
        seen_order_ids: set[int] = set()
        for snapshot in snapshots:
            order_id = snapshot.order_id
            if order_id is None or order_id <= 0:
                continue
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
            order_ids.append(order_id)

        if not order_ids:
            print("No cancelable broker orders found (missing order_id).")
            return

        requested_ids: list[int] = []
        failed_lines: list[str] = []
        print(f"Cancelling {len(order_ids)} active broker order(s) (scope={scope})")
        for order_id in order_ids:
            try:
                await self._order_service.cancel_order(OrderCancelSpec(order_id=order_id))
            except OrderValidationError as exc:
                failed_lines.append(f"order_id={order_id} rejected={exc}")
                continue
            except Exception as exc:
                failed_lines.append(f"order_id={order_id} error={exc}")
                continue
            requested_ids.append(order_id)

        print(
            "Broker cancel-all requested: "
            f"requested={len(requested_ids)} failed={len(failed_lines)} scope={scope}"
        )
        for line in failed_lines:
            print(f"Cancel failed: {line}")

        if not requested_ids:
            return

        # Give broker status callbacks a brief moment, then reconcile what remains active.
        await asyncio.sleep(0.3)
        try:
            remaining = await self._active_orders_service.list_active_orders(
                account=account,
                scope=scope,
            )
        except Exception as exc:
            print(f"Broker cancel-all verification skipped: {exc}")
            return

        remaining_by_id: dict[int, ActiveOrderSnapshot] = {}
        for snapshot in remaining:
            order_id = snapshot.order_id
            if order_id is None or order_id <= 0:
                continue
            if order_id not in requested_ids:
                continue
            remaining_by_id[order_id] = snapshot

        if not remaining_by_id:
            print("Broker cancel-all result: all requested orders are no longer active.")
            return

        detail = ", ".join(
            f"{order_id}:{(remaining_by_id[order_id].status or '-').strip() or '-'}"
            for order_id in sorted(remaining_by_id)
        )
        print(
            "Broker cancel-all result: "
            f"still_active={len(remaining_by_id)} ids=[{detail}]"
        )

    async def _cmd_trades(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        log_path = _resolve_event_log_path()
        if not log_path:
            print("Event log path not configured.")
            return
        log_path = os.path.expanduser(log_path)
        if not os.path.exists(log_path):
            print(f"No event log found at {log_path}.")
            return
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        today = datetime.now().astimezone().date()
        fills_rows: list[list[str]] = []
        lifecycle_rows: list[list[str]] = []
        completed_rows: list[list[str]] = []

        tracked_types = {
            "BreakoutConfirmed",
            "BreakoutStopped",
            "OrderIdAssigned",
            "OrderStatusChanged",
            "OrderFilled",
            "BracketChildOrderBrokerSnapshot",
            "BracketChildOrderStatusChanged",
            "BracketChildOrderFilled",
            "LadderProtectionStateChanged",
            "LadderStopLossReplaceFailed",
            "LadderStopLossReplaced",
            "IbGatewayLog",
        }
        sessions_by_key: dict[str, _BreakoutTradesSession] = {}
        session_keys_by_tag: dict[str, list[str]] = {}
        next_session_index_by_tag: dict[str, int] = {}
        unassigned_session_key_by_tag: dict[str, str] = {}
        session_key_by_entry_order_id: dict[int, str] = {}
        session_key_by_child_order_id: dict[int, str] = {}
        entry_filled_cumulative_by_order_id: dict[int, float] = {}
        child_filled_cumulative_by_order_id: dict[int, float] = {}

        def _new_session(tag: str, symbol: str, timestamp_local: datetime) -> _BreakoutTradesSession:
            next_index = next_session_index_by_tag.get(tag, 0) + 1
            next_session_index_by_tag[tag] = next_index
            key = f"{tag}#{next_index}"
            session = _BreakoutTradesSession(
                key=key,
                tag=tag,
                symbol=symbol or "-",
                confirm_time=timestamp_local,
            )
            sessions_by_key[key] = session
            session_keys_by_tag.setdefault(tag, []).append(key)
            unassigned_session_key_by_tag[tag] = key
            return session

        def _latest_session_for_tag(tag: str) -> Optional[_BreakoutTradesSession]:
            keys = session_keys_by_tag.get(tag)
            if not keys:
                return None
            return sessions_by_key.get(keys[-1])

        def _resolve_session(
            *,
            tag: Optional[str],
            symbol: str,
            timestamp_local: datetime,
            order_id: Optional[int] = None,
            parent_order_id: Optional[int] = None,
            create_if_missing: bool = False,
        ) -> Optional[_BreakoutTradesSession]:
            if order_id is not None:
                key = session_key_by_entry_order_id.get(order_id) or session_key_by_child_order_id.get(order_id)
                if key:
                    return sessions_by_key.get(key)
            if parent_order_id is not None:
                key = session_key_by_entry_order_id.get(parent_order_id)
                if key:
                    return sessions_by_key.get(key)
            if not tag or not _is_breakout_client_tag(tag):
                return None
            unassigned_key = unassigned_session_key_by_tag.get(tag)
            if unassigned_key:
                unassigned = sessions_by_key.get(unassigned_key)
                if unassigned is not None:
                    return unassigned
            existing = _latest_session_for_tag(tag)
            if existing is not None:
                return existing
            if not create_if_missing:
                return None
            return _new_session(tag=tag, symbol=symbol, timestamp_local=timestamp_local)

        def _record_fill_row(
            *,
            timestamp_local: datetime,
            fill_type: str,
            symbol: object,
            side: object,
            qty: Optional[float],
            price: Optional[float],
            status: object,
            order_id: object,
            tag: object,
        ) -> None:
            if qty is None or qty <= 0:
                return
            fills_rows.append(
                [
                    timestamp_local.strftime("%H:%M:%S.%f")[:-4],
                    str(fill_type),
                    str(symbol or "-"),
                    str(side or "-"),
                    _format_number(qty),
                    _format_number(price),
                    str(status or "-"),
                    str(order_id or "-"),
                    str(tag or "-"),
                ]
            )

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
                if event_type not in tracked_types:
                    continue
                event = payload.get("event")
                if not isinstance(event, dict):
                    continue
                timestamp = _parse_jsonl_timestamp(event.get("timestamp"), local_tz)
                if not timestamp:
                    continue
                timestamp_local = timestamp.astimezone(local_tz)
                if timestamp_local.date() != today:
                    continue

                if event_type == "BreakoutConfirmed":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    session = _new_session(tag=tag, symbol=symbol, timestamp_local=timestamp_local)
                    session.level = _maybe_float(event.get("level"))
                    parsed_tps = _coerce_float_list(event.get("take_profits"))
                    if parsed_tps:
                        session.configured_tps = parsed_tps
                    else:
                        single_tp = _maybe_float(event.get("take_profit"))
                        if single_tp is not None:
                            session.configured_tps = [single_tp]
                    session.configured_sl = _maybe_float(event.get("stop_loss"))
                    continue

                if event_type == "BreakoutStopped":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.watcher_stop_time = timestamp_local
                    if event.get("reason"):
                        session.watcher_stop_reason = str(event.get("reason"))
                    continue

                if event_type == "OrderIdAssigned":
                    spec = event.get("spec")
                    if not isinstance(spec, dict):
                        spec = {}
                    tag_value = spec.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(spec.get("symbol") or "-")
                    order_id = _maybe_int(event.get("order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = symbol
                    session.entry_side = str(spec.get("side") or session.entry_side or "")
                    planned_qty = _maybe_float(spec.get("qty"))
                    if planned_qty is not None and planned_qty > 0:
                        session.planned_entry_qty = planned_qty
                    if order_id is not None:
                        session.entry_order_id = order_id
                        session_key_by_entry_order_id[order_id] = session.key
                    unassigned_session_key_by_tag.pop(tag, None)
                    continue

                if event_type == "OrderStatusChanged":
                    spec = event.get("spec")
                    if not isinstance(spec, dict):
                        spec = {}
                    tag_value = spec.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(spec.get("symbol") or "-")
                    order_id = _maybe_int(event.get("order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        order_id=order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = symbol
                    if order_id is not None and session.entry_order_id is None:
                        session.entry_order_id = order_id
                        session_key_by_entry_order_id[order_id] = session.key
                    if event.get("status"):
                        session.entry_last_status = str(event.get("status"))
                    continue

                if event_type == "OrderFilled":
                    spec = event.get("spec")
                    if not isinstance(spec, dict):
                        spec = {}
                    symbol = spec.get("symbol") or "-"
                    side = spec.get("side") or "-"
                    price = _coalesce_number(event.get("avg_fill_price"), spec.get("limit_price"))
                    status = event.get("status") or "-"
                    order_id = event.get("order_id") or "-"
                    tag = spec.get("client_tag") or "-"
                    order_id_int = _maybe_int(event.get("order_id"))
                    cumulative_qty = _maybe_float(event.get("filled_qty"))
                    delta_qty = None
                    if _is_fill_event(status, cumulative_qty):
                        delta_qty = _delta_from_cumulative(
                            cumulative_qty,
                            order_id=order_id_int,
                            snapshots_by_order_id=entry_filled_cumulative_by_order_id,
                        )
                    _record_fill_row(
                        timestamp_local=timestamp_local,
                        fill_type="entry",
                        symbol=symbol,
                        side=side,
                        qty=delta_qty,
                        price=price,
                        status=status,
                        order_id=order_id,
                        tag=tag,
                    )

                    tag_value = spec.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag_text = str(tag_value)
                    session = _resolve_session(
                        tag=tag_text,
                        symbol=str(symbol),
                        timestamp_local=timestamp_local,
                        order_id=order_id_int,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = str(symbol)
                    session.entry_side = str(side)
                    planned_qty = _maybe_float(spec.get("qty"))
                    if planned_qty is not None and planned_qty > 0:
                        session.planned_entry_qty = planned_qty
                    if order_id_int is not None:
                        session_key_by_entry_order_id[order_id_int] = session.key
                        if session.entry_order_id is None:
                            session.entry_order_id = order_id_int
                    if cumulative_qty is not None and cumulative_qty > session.entry_filled_qty:
                        session.entry_filled_qty = cumulative_qty
                    remaining_qty = _maybe_float(event.get("remaining_qty"))
                    if remaining_qty is not None:
                        session.entry_remaining_qty = remaining_qty
                    if status:
                        session.entry_last_status = str(status)
                    if delta_qty is not None and delta_qty > 0:
                        if session.entry_first_fill_time is None:
                            session.entry_first_fill_time = timestamp_local
                        if price is not None:
                            session.entry_notional += delta_qty * price
                    avg_fill_price = _maybe_float(event.get("avg_fill_price"))
                    if avg_fill_price is not None and avg_fill_price > 0:
                        session.entry_avg_price = avg_fill_price
                    elif session.entry_filled_qty > 0 and session.entry_notional > 0:
                        session.entry_avg_price = session.entry_notional / session.entry_filled_qty
                    continue

                if event_type == "BracketChildOrderBrokerSnapshot":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    order_id_int = _maybe_int(event.get("order_id"))
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        order_id=order_id_int,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = symbol
                    kind = str(event.get("kind") or "")
                    expected_qty = _maybe_float(event.get("expected_qty"))
                    if _is_tp_kind(kind) and expected_qty is not None:
                        session.tp_expected_by_kind[kind] = expected_qty
                    if order_id_int is not None:
                        session_key_by_child_order_id[order_id_int] = session.key
                    continue

                if event_type == "BracketChildOrderStatusChanged":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    order_id_int = _maybe_int(event.get("order_id"))
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        order_id=order_id_int,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = symbol
                    if order_id_int is not None:
                        session_key_by_child_order_id[order_id_int] = session.key
                    kind = str(event.get("kind") or "")
                    status = str(event.get("status") or "")
                    if kind:
                        session.child_status_by_kind[kind] = status
                    continue

                if event_type == "BracketChildOrderFilled":
                    kind = event.get("kind") or "-"
                    symbol = event.get("symbol") or "-"
                    side = event.get("side") or "-"
                    price = _coalesce_number(event.get("avg_fill_price"), event.get("price"))
                    status = event.get("status") or "-"
                    order_id = event.get("order_id") or "-"
                    tag = event.get("client_tag") or "-"
                    order_id_int = _maybe_int(event.get("order_id"))
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    cumulative_qty = _maybe_float(event.get("filled_qty"))
                    delta_qty = None
                    if _is_fill_event(status, cumulative_qty):
                        delta_qty = _delta_from_cumulative(
                            cumulative_qty,
                            order_id=order_id_int,
                            snapshots_by_order_id=child_filled_cumulative_by_order_id,
                        )
                    _record_fill_row(
                        timestamp_local=timestamp_local,
                        fill_type=_format_kind(kind),
                        symbol=symbol,
                        side=side,
                        qty=delta_qty,
                        price=price,
                        status=status,
                        order_id=order_id,
                        tag=tag,
                    )

                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag_text = str(tag_value)
                    session = _resolve_session(
                        tag=tag_text,
                        symbol=str(symbol),
                        timestamp_local=timestamp_local,
                        order_id=order_id_int,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.symbol = str(symbol)
                    if order_id_int is not None:
                        session_key_by_child_order_id[order_id_int] = session.key
                    kind_text = str(kind)
                    session.child_status_by_kind[kind_text] = str(status)
                    if delta_qty is None or delta_qty <= 0:
                        continue
                    if session.first_exit_time is None:
                        session.first_exit_time = timestamp_local
                    session.last_exit_time = timestamp_local
                    if _is_tp_kind(kind_text):
                        session.tp_filled_qty += delta_qty
                        if price is not None:
                            session.tp_notional += delta_qty * price
                    elif _is_stop_kind(kind_text):
                        session.stop_filled_qty += delta_qty
                        if price is not None:
                            session.stop_notional += delta_qty * price
                    continue

                if event_type == "LadderProtectionStateChanged":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.protection_state = str(event.get("state") or "")
                    session.protection_reason = str(event.get("reason") or "")
                    active_tp_ids = event.get("active_take_profit_order_ids")
                    if isinstance(active_tp_ids, list):
                        for raw_order_id in active_tp_ids:
                            order_id = _maybe_int(raw_order_id)
                            if order_id is None:
                                continue
                            session_key_by_child_order_id[order_id] = session.key
                    stop_order_id = _maybe_int(event.get("stop_order_id"))
                    if stop_order_id is not None:
                        session_key_by_child_order_id[stop_order_id] = session.key
                    continue

                if event_type == "LadderStopLossReplaceFailed":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    session.replace_failed_count += 1
                    session.last_replace_failed_status = str(event.get("status") or "")
                    session.last_replace_failed_code = _maybe_int(event.get("broker_code"))
                    if event.get("broker_message"):
                        session.last_replace_failed_message = str(event.get("broker_message"))
                    _append_breakout_issue(session, "stop_replace_failed")
                    continue

                if event_type == "LadderStopLossReplaced":
                    tag_value = event.get("client_tag")
                    if not _is_breakout_client_tag(tag_value):
                        continue
                    tag = str(tag_value)
                    symbol = str(event.get("symbol") or "-")
                    parent_order_id = _maybe_int(event.get("parent_order_id"))
                    session = _resolve_session(
                        tag=tag,
                        symbol=symbol,
                        timestamp_local=timestamp_local,
                        parent_order_id=parent_order_id,
                        create_if_missing=True,
                    )
                    if session is None:
                        continue
                    if session.replace_failed_count > 0:
                        session.replace_failed_count = max(session.replace_failed_count - 1, 0)
                    continue

                if event_type == "IbGatewayLog":
                    req_id = _maybe_int(event.get("req_id"))
                    code = _maybe_int(event.get("code"))
                    if req_id is None or code is None:
                        continue
                    key = session_key_by_child_order_id.get(req_id) or session_key_by_entry_order_id.get(req_id)
                    if not key:
                        continue
                    session = sessions_by_key.get(key)
                    if session is None:
                        continue
                    if code == 404:
                        _append_breakout_issue(session, f"404_locate_hold:{req_id}")
                    elif code == 201:
                        _append_breakout_issue(session, f"201_reject:{req_id}")
                    elif code == 202:
                        message = str(event.get("message") or "")
                        if "cannot accept an order at a limit price" in message.lower():
                            _append_breakout_issue(session, f"202_price_band:{req_id}")

        sorted_sessions = sorted(
            sessions_by_key.values(),
            key=lambda item: item.confirm_time
            or item.entry_first_fill_time
            or item.watcher_stop_time
            or datetime.min.replace(tzinfo=local_tz),
        )
        for session in sorted_sessions:
            exit_qty = session.tp_filled_qty + session.stop_filled_qty
            open_qty = (
                max(session.entry_filled_qty - exit_qty, 0.0) if session.entry_filled_qty > 0 else None
            )
            issue_text = ",".join(session.issues) if session.issues else "-"
            lifecycle_rows.append(
                [
                    session.symbol,
                    _format_number(session.level),
                    _format_breakout_levels(session.configured_tps),
                    _format_number(session.configured_sl),
                    _format_time_value(session.entry_first_fill_time),
                    _format_number(session.entry_filled_qty if session.entry_filled_qty > 0 else None),
                    _format_number(session.entry_avg_price),
                    _format_number(exit_qty if exit_qty > 0 else None),
                    _format_number(open_qty),
                    _breakout_legs_summary(session),
                    _breakout_protection_summary(session),
                    session.watcher_stop_reason or "-",
                    issue_text,
                ]
            )

            if not _session_entry_is_fully_filled(session):
                continue
            if session.entry_filled_qty <= 0:
                continue
            if exit_qty + 1e-9 < session.entry_filled_qty:
                continue
            entry_price = session.entry_avg_price
            if entry_price is None and session.entry_filled_qty > 0 and session.entry_notional > 0:
                entry_price = session.entry_notional / session.entry_filled_qty
            exit_notional = session.tp_notional + session.stop_notional
            exit_price = (
                exit_notional / exit_qty
                if exit_qty > 0 and exit_notional > 0
                else None
            )
            pnl = _compute_pnl(session.entry_side, entry_price, exit_price, session.entry_filled_qty)
            completed_rows.append(
                [
                    _format_time_value(session.last_exit_time),
                    session.symbol,
                    str(session.entry_side or "-"),
                    _format_number(session.entry_filled_qty),
                    _format_number(entry_price),
                    _format_number(exit_price),
                    _format_number(pnl),
                    _breakout_outcome_kind(session),
                    session.tag,
                    _format_time_value(session.entry_first_fill_time),
                ]
            )

        print(f"Trades for {today.isoformat()} (local time).")
        print("Fills today (execution deltas):")
        if fills_rows:
            for line in _format_simple_table(
                [
                    "time",
                    "type",
                    "symbol",
                    "side",
                    "qty",
                    "price",
                    "status",
                    "order_id",
                    "tag",
                ],
                fills_rows,
            ):
                print(line)
        else:
            print("No fills found today.")
        print("Breakout lifecycle today:")
        if lifecycle_rows:
            for line in _format_simple_table(
                [
                    "symbol",
                    "level",
                    "tp",
                    "sl",
                    "entry_time",
                    "entry_qty",
                    "entry_px",
                    "exit_qty",
                    "open_qty",
                    "legs",
                    "protection",
                    "watcher",
                    "issues",
                ],
                lifecycle_rows,
            ):
                print(line)
        else:
            print("No breakout sessions found today.")
        print("Completed breakout trades (full exits only):")
        if completed_rows:
            for line in _format_simple_table(
                [
                    "exit_time",
                    "symbol",
                    "side",
                    "qty",
                    "entry",
                    "exit",
                    "pnl",
                    "kind",
                    "tag",
                    "entry_time",
                ],
                completed_rows,
            ):
                print(line)
        else:
            print("No completed breakout trades found today.")

    async def _seed_position_origins(self) -> None:
        if not self._position_origin_tracker:
            return
        if not self._connection.status().get("connected"):
            return
        timeout = self._connection.config.timeout
        try:
            count = await self._position_origin_tracker.seed_from_ibkr(
                self._connection.ib,
                timeout=timeout,
            )
        except Exception as exc:
            _print_exception("Position tag seed from IBKR failed", exc)
            count = 0
        if count:
            print(f"Position tags loaded from IBKR executions: {count}")
            return
        fallback = self._position_origin_tracker.seed_from_jsonl()
        if fallback:
            print(f"Position tags loaded from event log: {fallback}")

    async def _reconcile_orphan_exit_orders(self, *, trigger: str) -> None:
        if not self._active_orders_service or not self._positions_service:
            return
        if not self._connection.status().get("connected"):
            return

        async with self._orphan_exit_lock:
            scope = self._orphan_exit_scope
            action = self._orphan_exit_action
            auto_cancel = action == "cancel" and self._order_service is not None

            try:
                active_orders = await self._active_orders_service.list_active_orders(scope=scope)
            except Exception as exc:
                _print_exception("Orphan exit reconciliation failed (active orders)", exc)
                return

            try:
                positions = await self._positions_service.list_positions()
            except Exception as exc:
                _print_exception("Orphan exit reconciliation failed (positions)", exc)
                positions = []

            position_qty_by_account_symbol: dict[tuple[str, str], float] = {}
            position_qty_by_symbol: dict[str, float] = {}
            for position in positions:
                symbol = position.symbol.strip().upper()
                if not symbol:
                    continue
                account = _normalize_account_key(position.account)
                qty = float(position.qty or 0.0)
                position_qty_by_account_symbol[(account, symbol)] = (
                    position_qty_by_account_symbol.get((account, symbol), 0.0) + qty
                )
                position_qty_by_symbol[symbol] = position_qty_by_symbol.get(symbol, 0.0) + qty

            orphan_orders: list[ActiveOrderSnapshot] = []
            for order in active_orders:
                if (order.side or "").strip().upper() != "SELL":
                    continue
                if order.parent_order_id is None:
                    continue
                symbol = (order.symbol or "").strip().upper()
                if not symbol:
                    continue
                account = _normalize_account_key(order.account)
                qty = position_qty_by_account_symbol.get((account, symbol))
                if qty is None:
                    qty = position_qty_by_symbol.get(symbol, 0.0)
                if abs(qty) > 1e-9:
                    continue
                orphan_orders.append(order)
                if self._event_bus:
                    self._event_bus.publish(
                        OrphanExitOrderDetected.now(
                            trigger=trigger,
                            action=action,
                            scope=scope,
                            order_id=order.order_id,
                            parent_order_id=order.parent_order_id,
                            account=order.account,
                            symbol=symbol,
                            status=order.status,
                            remaining_qty=order.remaining_qty,
                            client_tag=order.client_tag,
                        )
                    )

            cancelled_count = 0
            cancel_failed_count = 0
            if auto_cancel:
                for order in orphan_orders:
                    if order.order_id is None:
                        continue
                    try:
                        ack = await self._order_service.cancel_order(OrderCancelSpec(order_id=order.order_id))
                    except Exception as exc:
                        cancel_failed_count += 1
                        if self._event_bus:
                            self._event_bus.publish(
                                OrphanExitOrderCancelFailed.now(
                                    trigger=trigger,
                                    order_id=order.order_id,
                                    account=order.account,
                                    symbol=order.symbol,
                                    error_type=type(exc).__name__,
                                    message=str(exc),
                                )
                            )
                        continue
                    cancelled_count += 1
                    if self._event_bus:
                        self._event_bus.publish(
                            OrphanExitOrderCancelled.now(
                                trigger=trigger,
                                order_id=order.order_id,
                                status=ack.status,
                                account=order.account,
                                symbol=order.symbol,
                            )
                        )

            if orphan_orders:
                print(
                    "Orphan exit reconciliation: "
                    f"found={len(orphan_orders)} action={action} "
                    f"cancelled={cancelled_count} failed={cancel_failed_count}"
                )

            if self._event_bus:
                self._event_bus.publish(
                    OrphanExitReconciliationCompleted.now(
                        trigger=trigger,
                        scope=scope,
                        action=action,
                        active_order_count=len(active_orders),
                        position_count=len(positions),
                        orphan_count=len(orphan_orders),
                        cancelled_count=cancelled_count,
                        cancel_failed_count=cancel_failed_count,
                    )
                )

    async def _cmd_positions(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        if not self._positions_service:
            print("Positions service not configured.")
            return
        if not self._connection.status().get("connected"):
            print("Not connected. Use `connect` before requesting positions.")
            return
        account = _kwargs.get("account") or _config_get(self._config, "account")
        positions = await self._positions_service.list_positions(account=account)
        if not positions:
            print("No positions found.")
            return
        tag_lookup = None
        exit_lookup = None
        take_profits_lookup = None
        if self._position_origin_tracker:
            tag_lookup = self._position_origin_tracker.tag_for
            exit_lookup = self._position_origin_tracker.exit_levels_for
            take_profits_lookup = self._position_origin_tracker.take_profits_for
        for line in _format_positions_table(
            positions,
            tag_lookup=tag_lookup,
            exit_lookup=exit_lookup,
            take_profits_lookup=take_profits_lookup,
        ):
            print(line)

    def _print_breakout_status(self) -> None:
        if not self._breakout_tasks:
            print("No breakout watchers running.")
            return
        for name, (config, task) in sorted(self._breakout_tasks.items()):
            state = "running" if not task.done() else "done"
            extras = []
            if config.take_profits:
                levels = ",".join(f"{level:g}" for level in config.take_profits)
                extras.append(f"tp=[{levels}]")
                if config.take_profit_qtys:
                    qtys = ",".join(str(item) for item in config.take_profit_qtys)
                    extras.append(f"tp_qtys=[{qtys}]")
            elif config.take_profit is not None:
                extras.append(f"tp={config.take_profit}")
            if config.stop_loss is not None:
                extras.append(f"sl={config.stop_loss}")
            if config.tp_reprice_on_fill:
                extras.append("tp_reprice=on_fill")
            suffix = f" {' '.join(extras)}" if extras else ""
            print(
                f"{name} level={config.rule.level} "
                f"qty={config.qty} state={state}{suffix}"
            )

    def _stream_health_summary(
        self,
        symbol: str,
        *,
        bar_size: str,
        use_rth: bool,
    ) -> Optional[str]:
        if not self._bar_stream:
            return None
        get_stream_health = getattr(self._bar_stream, "get_stream_health", None)
        if not callable(get_stream_health):
            return None
        payload = get_stream_health(symbol, bar_size=bar_size, use_rth=use_rth)
        if not isinstance(payload, dict):
            return "inactive"
        status = str(payload.get("status") or "unknown")
        silence_value = payload.get("silence_seconds")
        timeout_value = payload.get("timeout_seconds")
        blocked_message = payload.get("blocked_message")
        if status == "blocked_competing_session":
            if isinstance(blocked_message, str) and blocked_message.strip():
                return f"blocked({blocked_message.strip()})"
            return "blocked"
        if status == "stalled":
            if isinstance(silence_value, (int, float)) and isinstance(timeout_value, (int, float)):
                return f"stalled({silence_value:.1f}s>{timeout_value:.1f}s)"
            return "stalled"
        if status == "healthy":
            if isinstance(silence_value, (int, float)):
                return f"healthy({silence_value:.1f}s)"
            return "healthy"
        return status

    async def _stop_breakouts(
        self,
        symbol: Optional[str] = None,
        *,
        persist: bool = False,
    ) -> None:
        if not self._breakout_tasks:
            if symbol and not persist:
                print(f"No breakout watchers found for {symbol.strip().upper()}.")
            return
        symbol_filter = symbol.strip().upper() if symbol else None
        if persist and symbol_filter:
            persist = False
        targets = []
        for name, (config, task) in self._breakout_tasks.items():
            if symbol_filter and config.symbol != symbol_filter:
                continue
            targets.append((name, config, task))
        if not targets:
            if symbol_filter and not persist:
                print(f"No breakout watchers found for {symbol_filter}.")
            return
        if persist:
            self._save_breakout_state([config for _, config, _ in targets])
            self._suspend_breakout_state_updates = True
        for _name, _config, task in targets:
            if _config.client_tag:
                self._drop_tp_reprice_session(_config.client_tag)
            task.cancel()
        await asyncio.gather(*(task for _, _, task in targets), return_exceptions=True)
        for name, _config, _task in targets:
            self._breakout_tasks.pop(name, None)
        if not persist:
            self._persist_breakout_state()
        print(f"Stopped {len(targets)} breakout watcher(s).")

    def _on_breakout_done(self, task_name: str, task: asyncio.Task) -> None:
        config_task = self._breakout_tasks.pop(task_name, None)
        if not self._suspend_breakout_state_updates:
            self._persist_breakout_state()
        if config_task is not None:
            config, _ = config_task
            if config.client_tag and (task.cancelled() or task.exception() is not None):
                self._drop_tp_reprice_session(config.client_tag)
        if task.cancelled():
            print(f"Breakout watcher cancelled: {task_name}")
            return
        exc = task.exception()
        if exc:
            _print_exception(f"Breakout watcher failed: {task_name}", exc)
            return
        print(f"Breakout watcher finished: {task_name}")

    def _persist_breakout_state(self) -> None:
        if self._suspend_breakout_state_updates:
            return
        configs = [config for config, _task in self._breakout_tasks.values()]
        self._save_breakout_state(configs)

    def _save_breakout_state(self, configs: list[BreakoutRunConfig]) -> None:
        if not self._breakout_state_path:
            return
        if not configs:
            self._clear_breakout_state()
            return
        payload = {
            "version": _BREAKOUT_STATE_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "breakouts": [_serialize_breakout_config(config) for config in configs],
        }
        try:
            self._breakout_state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._breakout_state_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        except Exception as exc:
            print(f"Failed to write breakout state: {exc}")

    def _clear_breakout_state(self) -> None:
        if not self._breakout_state_path:
            return
        try:
            if self._breakout_state_path.exists():
                self._breakout_state_path.unlink()
        except Exception as exc:
            print(f"Failed to clear breakout state: {exc}")

    def _load_breakout_state(self) -> list[BreakoutRunConfig]:
        if not self._breakout_state_path:
            return []
        if not self._breakout_state_path.exists():
            return []
        try:
            with self._breakout_state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            print(f"Failed to read breakout state: {exc}")
            return []
        entries: list[object]
        if isinstance(payload, dict):
            entries = payload.get("breakouts", [])
        elif isinstance(payload, list):
            entries = payload
        else:
            print("Breakout state file has an invalid format.")
            return []
        if not isinstance(entries, list):
            print("Breakout state file has an invalid format.")
            return []
        configs: list[BreakoutRunConfig] = []
        invalid_entries = 0
        for entry in entries:
            if not isinstance(entry, dict):
                invalid_entries += 1
                continue
            config = _deserialize_breakout_config(entry)
            if config is None:
                invalid_entries += 1
                continue
            configs.append(config)
        if invalid_entries:
            print(f"Skipped {invalid_entries} invalid breakout state entries.")
        return configs

    def _format_breakout_summary(self, config: BreakoutRunConfig) -> str:
        parts = [
            f"symbol={config.symbol}",
            f"level={config.rule.level:g}",
            f"qty={config.qty}",
            f"entry={config.entry_type.value.lower()}",
            f"bar={config.bar_size}",
            f"fast={'true' if config.rule.fast_entry.enabled else 'false'}",
        ]
        if config.take_profits:
            levels = ",".join(f"{level:g}" for level in config.take_profits)
            parts.append(f"tp=[{levels}]")
            if config.take_profit_qtys:
                qtys = ",".join(str(item) for item in config.take_profit_qtys)
                parts.append(f"tp_qtys=[{qtys}]")
        elif config.take_profit is not None:
            parts.append(f"tp={config.take_profit:g}")
        if config.stop_loss is not None:
            parts.append(f"sl={config.stop_loss:g}")
        if config.ladder_execution_mode != LadderExecutionMode.ATTACHED:
            parts.append(f"tp_exec={_ladder_execution_mode_label(config.ladder_execution_mode)}")
        if config.tp_reprice_on_fill:
            parts.append("tp_reprice=on_fill")
        if config.max_bars is not None:
            parts.append(f"max_bars={config.max_bars}")
        if config.tif:
            parts.append(f"tif={config.tif}")
        parts.append(f"outside_rth={'true' if config.outside_rth else 'false'}")
        if config.account:
            parts.append(f"account={config.account}")
        if config.client_tag:
            parts.append(f"client_tag={config.client_tag}")
        return " ".join(parts)

    async def _cmd_ingest_flex(self, args: list[str], kwargs: dict[str, str]) -> None:
        if not self._pnl_service:
            print("PnL service not configured.")
            return
        csv_value = kwargs.get("csv") or (args[0] if args else None)
        account = kwargs.get("account") or _config_get(self._config, "account")
        source = kwargs.get("source") or "flex"
        if not csv_value or not account:
            print("Usage: ingest-flex csv=... account=... [source=flex]")
            return
        csv_path = Path(csv_value).expanduser()
        try:
            result = await asyncio.to_thread(
                self._pnl_service.ingest_flex,
                csv_path,
                account,
                source,
            )
        except Exception as exc:
            print(f"Ingest failed: {exc}")
            return
        print(
            "Ingested "
            f"{result.days_ingested} days "
            f"(rows read={result.rows_read}, used={result.rows_used}) "
            f"from {result.csv_path}"
        )

    async def _cmd_pnl_import(self, args: list[str], kwargs: dict[str, str]) -> None:
        await self._pnl_import(args, kwargs)

    async def _pnl_import(self, args: list[str], kwargs: dict[str, str]) -> bool:
        if not self._pnl_service:
            print("PnL service not configured.")
            return False
        account = os.getenv("PNL_ACCOUNT") or "paper"
        source = kwargs.get("source") or "flex"
        csv_value = kwargs.get("csv") or (args[0] if args else None)
        csv_path = None
        if csv_value:
            csv_path = Path(csv_value).expanduser()
        else:
            try:
                from apps.adapters.pnl.gmail_flex_fetcher import (
                    GmailFlexConfig,
                    fetch_latest_flex_report,
                )
            except ImportError as exc:
                print(f"Gmail fetcher not available: {exc}")
                return False
            try:
                config = GmailFlexConfig.from_env()
                csv_path = await asyncio.to_thread(fetch_latest_flex_report, config)
            except Exception as exc:
                print(f"Gmail fetch failed: {exc}")
                return False
        try:
            result = await asyncio.to_thread(
                self._pnl_service.ingest_flex,
                csv_path,
                account,
                source,
            )
        except Exception as exc:
            print(f"Ingest failed: {exc}")
            return False
        print(
            "Ingested "
            f"{result.days_ingested} days "
            f"(rows read={result.rows_read}, used={result.rows_used}) "
            f"from {result.csv_path}"
        )
        return True

    async def _cmd_pnl_open(self, _args: list[str], kwargs: dict[str, str]) -> None:
        api_port = _parse_port(kwargs.get("api_port"), default=int(os.getenv("API_PORT", "8000")))
        web_port = _parse_port(kwargs.get("web_port"), default=int(os.getenv("WEB_PORT", "5173")))
        account = os.getenv("PNL_ACCOUNT") or "paper"
        api_proc = await self._ensure_process(
            name="pnl_api",
            cmd=[
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.main:app",
                "--reload",
                "--port",
                str(api_port),
            ],
        )
        if api_proc is None:
            return
        web_env = os.environ.copy()
        web_env["VITE_API_BASE_URL"] = f"http://localhost:{api_port}"
        if account:
            web_env.setdefault("VITE_DEFAULT_ACCOUNT", account)
        web_proc = await self._ensure_process(
            name="pnl_web",
            cmd=[
                "npm",
                "run",
                "dev",
                "--",
                "--port",
                str(web_port),
            ],
            cwd=Path("web"),
            env=web_env,
            stdin=asyncio.subprocess.DEVNULL,
        )
        if web_proc is None:
            return
        api_ready = await _wait_for_port("127.0.0.1", api_port, timeout=30.0)
        web_ready = await _wait_for_port("127.0.0.1", web_port, timeout=60.0)
        if not api_ready:
            print("API did not start in time. Check logs.")
            return
        if not web_ready:
            print("Web dev server did not start in time. Check logs.")
            return
        url = f"http://localhost:{web_port}"
        print(f"Opening calendar: {url}")
        webbrowser.open(url)

    async def _cmd_pnl_launch(self, args: list[str], kwargs: dict[str, str]) -> None:
        ok = await self._pnl_import(args, kwargs)
        if not ok:
            return
        await self._cmd_pnl_open(args, kwargs)

    async def _ensure_process(
        self,
        *,
        name: str,
        cmd: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[int] = None,
    ) -> Optional[asyncio.subprocess.Process]:
        existing = self._pnl_processes.get(name)
        if existing and existing.returncode is None:
            print(f"{name} already running (pid={existing.pid})")
            return existing
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=stdin,
            )
        except FileNotFoundError as exc:
            print(f"Failed to start {name}: {exc}")
            return None
        self._pnl_processes[name] = proc
        print(f"Started {name} (pid={proc.pid})")
        return proc

    async def _cmd_set(self, _args: list[str], kwargs: dict[str, str]) -> None:
        if not kwargs:
            print("Usage: set key=value [key=value ...]")
            return
        for key, value in kwargs.items():
            self._config[key] = value
        print("Config updated.")

    async def _cmd_show(self, args: list[str], _kwargs: dict[str, str]) -> None:
        if args and args[0].lower() != "config":
            print("Usage: show config")
            return
        account = self._config.get("account")
        visible_keys = [key for key in sorted(self._config) if key != "account"]
        if not self._config:
            print("Config: (empty)")
        else:
            for key in visible_keys:
                print(f"{key}={self._config[key]}")
        if visible_keys:
            print("")
        default_line = "Default"
        if account:
            default_line += f" account={account}"
        print(default_line)
        print("  buy/sell: tif=DAY outside_rth=false")
        print(
            "  breakout: bar_size=1 min fast=true fast_bar=1 secs use_rth=false "
            "outside_rth=!use_rth entry=limit tif=DAY quote_age/quote_max_age=2.0 "
            "tp_exec=auto(tp2->detached70,tp3->detached)"
        )

    async def _cmd_disconnect(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        self._connection.disconnect()
        print("Disconnected.")

    async def _cmd_status(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        status = self._connection.status()
        state = "connected" if status["connected"] else "disconnected"
        print(
            f"{state} - {status['host']}:{status['port']} "
            f"client_id={status['client_id']} readonly={status['readonly']}"
        )

    async def _cmd_quit(self, _args: list[str], _kwargs: dict[str, str]) -> None:
        await self._stop_pnl_processes()
        await self._stop_breakouts(persist=True)
        self._should_exit = True

    async def _cmd_refresh(self, args: list[str], kwargs: dict[str, str]) -> None:
        if args or kwargs:
            print("Usage: refresh")
            return
        await self._stop_pnl_processes()
        await self._stop_breakouts(persist=True)
        await self._stop_tp_reprice_tasks()
        self._connection.disconnect()

        orig_argv = getattr(sys, "orig_argv", None)
        if isinstance(orig_argv, list) and len(orig_argv) > 1:
            exec_args = [sys.executable, *orig_argv[1:]]
        else:
            main_module = sys.modules.get("__main__")
            package = getattr(main_module, "__package__", None)
            if package:
                exec_args = [sys.executable, "-m", str(package), *sys.argv[1:]]
            else:
                script_path = Path(sys.argv[0]).expanduser()
                if script_path.exists():
                    exec_args = [sys.executable, *sys.argv]
                else:
                    exec_args = [sys.executable, "-m", "apps.cli", *sys.argv[1:]]
        print("Refreshing CLI...")
        try:
            os.execv(sys.executable, exec_args)
        except OSError as exc:
            print(f"Refresh failed: {exc}")

    async def _stop_pnl_processes(self) -> None:
        if not self._pnl_processes:
            return
        for name, proc in list(self._pnl_processes.items()):
            if proc.returncode is not None:
                continue
            print(f"Stopping {name} (pid={proc.pid})...")
            proc.terminate()
        for name, proc in list(self._pnl_processes.items()):
            if proc.returncode is not None:
                continue
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                print(f"Stopped {name}.")
            except asyncio.TimeoutError:
                print(f"Force killing {name} (pid={proc.pid})...")
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    print(f"Failed to kill {name} (pid={proc.pid}).")

    def _log_cli_error(self, exc: BaseException, command: Optional[str], raw_input: str) -> None:
        event = CliErrorLogged.now(
            message=str(exc),
            error_type=type(exc).__name__,
            traceback=_format_traceback(exc),
            command=command,
            raw_input=raw_input,
        )
        if self._ops_logger:
            try:
                self._ops_logger(event)
                return
            except Exception:
                pass
        if self._event_bus:
            try:
                self._event_bus.publish(event)
            except Exception:
                pass

    async def _submit_order(
        self,
        side: OrderSide,
        args: list[str],
        kwargs: dict[str, str],
    ) -> None:
        if not self._order_service:
            print("Order service not configured.")
            return
        if len(args) > 2:
            print(
                "Usage: buy|sell SYMBOL qty=... [limit=...] [tif=DAY] "
                "[outside_rth=true|false] [account=...] [client_tag=...] "
                "| buy|sell SYMBOL QTY [limit=...]"
            )
            return
        symbol = args[0].strip().upper() if args else None
        if not symbol:
            symbol = (
                kwargs.get("symbol")
                or _config_get(self._config, "symbol")
            )
            if symbol:
                symbol = symbol.strip().upper()
        if not symbol:
            print(
                "Usage: buy|sell SYMBOL qty=... [limit=...] [tif=DAY] "
                "[outside_rth=true|false] [account=...] [client_tag=...] "
                "| buy|sell SYMBOL QTY [limit=...]"
            )
            return
        positional_qty = args[1] if len(args) > 1 else None
        qty_raw = kwargs.get("qty") or positional_qty or _config_get(self._config, "qty")
        if qty_raw is None:
            print(
                "Usage: buy|sell SYMBOL qty=... [limit=...] [tif=DAY] "
                "[outside_rth=true|false] [account=...] [client_tag=...] "
                "| buy|sell SYMBOL QTY [limit=...]"
            )
            return
        try:
            qty = int(qty_raw)
        except ValueError:
            print("qty must be an integer")
            return
        limit_raw = kwargs.get("limit")
        limit_price = float(limit_raw) if limit_raw is not None else None
        order_type = OrderType.LIMIT if limit_price is not None else OrderType.MARKET
        tif = kwargs.get("tif") or _config_get(self._config, "tif") or "DAY"
        outside_rth_value = kwargs.get("outside_rth") or _config_get(self._config, "outside_rth")
        outside_rth = _parse_bool(outside_rth_value) if outside_rth_value is not None else False
        account = kwargs.get("account") or _config_get(self._config, "account")
        client_tag = kwargs.get("client_tag") or _config_get(self._config, "client_tag")

        spec = OrderSpec(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            tif=tif,
            outside_rth=outside_rth,
            account=account,
            client_tag=client_tag,
        )
        try:
            ack = await self._order_service.submit_order(spec)
        except OrderValidationError as exc:
            print(f"Order rejected: {exc}")
            return
        print(f"Order submitted: order_id={ack.order_id} status={ack.status}")


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_entry_type(value: str) -> OrderType:
    normalized = value.strip().lower()
    if normalized in {"market", "mkt"}:
        return OrderType.MARKET
    if normalized in {"limit", "lmt"}:
        return OrderType.LIMIT
    raise ValueError("invalid entry type")


def _parse_ladder_execution_mode(value: object) -> LadderExecutionMode:
    if value is None:
        return LadderExecutionMode.ATTACHED
    if isinstance(value, LadderExecutionMode):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "attached"}:
            return LadderExecutionMode.ATTACHED
        if normalized in {"detached", "det"}:
            return LadderExecutionMode.DETACHED
        if normalized in {"detached70", "det70", "detached_70_30"}:
            return LadderExecutionMode.DETACHED_70_30
    raise ValueError("invalid ladder execution mode")


def _ladder_execution_mode_label(mode: LadderExecutionMode) -> str:
    if mode == LadderExecutionMode.DETACHED:
        return "detached"
    if mode == LadderExecutionMode.DETACHED_70_30:
        return "detached70"
    return "attached"


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value)
    return default


def _coerce_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_str(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_float_list(value: object) -> Optional[list[float]]:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    result: list[float] = []
    for item in value:
        as_float = _coerce_float(item)
        if as_float is None:
            return None
        result.append(as_float)
    return result


def _coerce_int_list(value: object) -> Optional[list[int]]:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    result: list[int] = []
    for item in value:
        as_int = _coerce_int(item)
        if as_int is None:
            return None
        result.append(as_int)
    return result


def _parse_tp_mode_token(value: str) -> Optional[int]:
    match = _TP_MODE_TOKEN.fullmatch(value.strip())
    if not match:
        return None
    return int(match.group(1))


def _default_breakout_client_tag(symbol: str, level: float) -> str:
    return f"breakout:{symbol}:{level:g}"


def _default_take_profit_ratios(count: int) -> list[float]:
    if count == 1:
        return [1.0]
    if count == 2:
        return [0.8, 0.2]
    if count == 3:
        return [0.6, 0.3, 0.1]
    raise ValueError("count must be 1, 2, or 3")


def _parse_take_profit_ratios(value: object, expected_count: int) -> Optional[list[float]]:
    if value is None:
        return None
    text = _coerce_str(value)
    if not text:
        return None
    parts = [part.strip() for part in text.split("-") if part.strip()]
    if len(parts) != expected_count:
        return None
    ratios: list[float] = []
    for part in parts:
        try:
            parsed = float(part)
        except ValueError:
            return None
        ratios.append(parsed)
    if any(ratio <= 0 for ratio in ratios):
        return None
    total = sum(ratios)
    if total <= 0:
        return None
    if total > 1.5:
        return [ratio / total for ratio in ratios]
    if abs(total - 1.0) > 0.05:
        return None
    return ratios


def _split_qty_by_ratios(total_qty: int, ratios: list[float]) -> list[int]:
    if total_qty <= 0:
        raise ValueError("qty must be greater than zero")
    if not ratios:
        raise ValueError("ratios are required")
    if any(ratio <= 0 for ratio in ratios):
        raise ValueError("ratios must be positive")
    total = sum(ratios)
    if total <= 0:
        raise ValueError("ratios sum must be positive")
    normalized = [ratio / total for ratio in ratios]
    raw = [total_qty * ratio for ratio in normalized]
    qtys = [int(value) for value in raw]
    remainder = total_qty - sum(qtys)
    if remainder > 0:
        fractions = [(idx, raw[idx] - qtys[idx]) for idx in range(len(qtys))]
        fractions.sort(key=lambda item: (-item[1], item[0]))
        for idx, _fraction in fractions[:remainder]:
            qtys[idx] += 1
    if any(qty <= 0 for qty in qtys):
        raise ValueError("qty too small for requested tp allocation")
    return qtys


def _validate_take_profit_levels(levels: list[float]) -> bool:
    if not levels:
        return False
    if any(level <= 0 for level in levels):
        return False
    for idx in range(1, len(levels)):
        if levels[idx] <= levels[idx - 1]:
            return False
    return True


def _tp_index_from_kind(kind: str) -> Optional[int]:
    normalized = kind.strip().lower()
    if normalized == "take_profit":
        return 1
    if normalized.startswith("take_profit_"):
        suffix = normalized.split("_")[-1]
        try:
            parsed = int(suffix)
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed
    return None


def _is_order_fully_filled(
    *,
    status: object,
    filled_qty: object,
    remaining_qty: object,
    expected_qty: int,
) -> bool:
    remaining = _maybe_float(remaining_qty)
    if remaining is not None and remaining <= 0:
        return True
    filled = _maybe_float(filled_qty)
    if filled is not None and filled >= expected_qty:
        return True
    if status:
        normalized = str(status).strip().lower()
        if normalized == "filled":
            return True
    return False


def _is_replace_ack_accepted(ack: object, *, expected_order_id: int) -> bool:
    if ack is None:
        return False
    ack_order_id = getattr(ack, "order_id", None)
    if ack_order_id != expected_order_id:
        return False
    status = getattr(ack, "status", None)
    if status is None:
        return False
    normalized = str(status).strip().lower()
    return normalized in _REPLACE_ACCEPTED_STATUSES


def _serialize_breakout_config(config: BreakoutRunConfig) -> dict[str, object]:
    return {
        "symbol": config.symbol,
        "qty": config.qty,
        "level": config.rule.level,
        "entry_type": config.entry_type.value,
        "take_profit": config.take_profit,
        "take_profits": config.take_profits,
        "take_profit_qtys": config.take_profit_qtys,
        "stop_loss": config.stop_loss,
        "use_rth": config.use_rth,
        "bar_size": config.bar_size,
        "fast_bar_size": config.fast_bar_size,
        "fast_enabled": config.rule.fast_entry.enabled,
        "max_bars": config.max_bars,
        "tif": config.tif,
        "outside_rth": config.outside_rth,
        "account": config.account,
        "client_tag": config.client_tag,
        "quote_max_age_seconds": config.quote_max_age_seconds,
        "tp_reprice_on_fill": config.tp_reprice_on_fill,
        "tp_reprice_bar_size": config.tp_reprice_bar_size,
        "tp_reprice_use_rth": config.tp_reprice_use_rth,
        "tp_reprice_timeout_seconds": config.tp_reprice_timeout_seconds,
        "ladder_execution_mode": config.ladder_execution_mode.value,
    }


def _deserialize_breakout_config(payload: dict[str, object]) -> Optional[BreakoutRunConfig]:
    symbol = _coerce_str(payload.get("symbol"))
    level = _coerce_float(payload.get("level"))
    qty = _coerce_int(payload.get("qty"))
    if not symbol or level is None or qty is None:
        return None
    if level <= 0 or qty <= 0:
        return None
    entry_type = OrderType.LIMIT
    entry_raw = payload.get("entry_type")
    if isinstance(entry_raw, str):
        try:
            entry_type = _parse_entry_type(entry_raw)
        except ValueError:
            return None
    take_profit = _coerce_float(payload.get("take_profit"))
    take_profits = _coerce_float_list(payload.get("take_profits"))
    if take_profits == []:
        take_profits = None
    if take_profits and len(take_profits) not in {2, 3}:
        return None
    take_profit_qtys = _coerce_int_list(payload.get("take_profit_qtys"))
    if take_profit_qtys == []:
        take_profit_qtys = None
    if take_profit_qtys is not None:
        if not take_profits:
            return None
        if len(take_profit_qtys) != len(take_profits):
            return None
        if any(qty <= 0 for qty in take_profit_qtys):
            return None
        if sum(take_profit_qtys) != qty:
            return None
    stop_loss = _coerce_float(payload.get("stop_loss"))
    if take_profit is not None and take_profits:
        return None
    if stop_loss is not None and take_profit is None and not take_profits:
        return None
    if stop_loss is None and (take_profit is not None or take_profits):
        return None
    use_rth = _coerce_bool(payload.get("use_rth"), default=False)
    bar_size = _coerce_str(payload.get("bar_size")) or "1 min"
    fast_bar_size = _coerce_str(payload.get("fast_bar_size")) or "1 secs"
    fast_enabled = _coerce_bool(payload.get("fast_enabled"), default=True)
    max_bars = _coerce_int(payload.get("max_bars"))
    tif = _coerce_str(payload.get("tif")) or "DAY"
    outside_rth = _coerce_bool(payload.get("outside_rth"), default=not use_rth)
    account = _coerce_str(payload.get("account"))
    client_tag = _coerce_str(payload.get("client_tag"))
    quote_max_age_seconds = _coerce_float(payload.get("quote_max_age_seconds")) or 2.0
    tp_reprice_on_fill = _coerce_bool(payload.get("tp_reprice_on_fill"), default=False)
    tp_reprice_bar_size = _coerce_str(payload.get("tp_reprice_bar_size")) or bar_size
    tp_reprice_use_rth = _coerce_bool(payload.get("tp_reprice_use_rth"), default=use_rth)
    tp_reprice_timeout_seconds = _coerce_float(payload.get("tp_reprice_timeout_seconds")) or 5.0
    mode_value = payload.get("ladder_execution_mode")
    if mode_value is None and take_profits:
        if len(take_profits) == 2:
            try:
                expected_two = _split_qty_by_ratios(qty, [0.7, 0.3])
            except ValueError:
                return None
            if take_profit_qtys == expected_two:
                ladder_execution_mode = LadderExecutionMode.DETACHED_70_30
            else:
                ladder_execution_mode = LadderExecutionMode.DETACHED
        elif len(take_profits) == 3:
            ladder_execution_mode = LadderExecutionMode.DETACHED
        else:
            ladder_execution_mode = LadderExecutionMode.ATTACHED
    else:
        try:
            ladder_execution_mode = _parse_ladder_execution_mode(mode_value)
        except ValueError:
            return None
    if tp_reprice_timeout_seconds <= 0:
        return None
    if tp_reprice_on_fill and not take_profits:
        return None
    if (
        ladder_execution_mode == LadderExecutionMode.DETACHED
        and (not take_profits or len(take_profits) != 3)
    ):
        return None
    if (
        ladder_execution_mode == LadderExecutionMode.DETACHED_70_30
        and (not take_profits or len(take_profits) != 2)
    ):
        return None
    if ladder_execution_mode == LadderExecutionMode.ATTACHED and take_profits:
        return None
    if ladder_execution_mode == LadderExecutionMode.DETACHED and tp_reprice_on_fill:
        tp_reprice_on_fill = False
    if ladder_execution_mode == LadderExecutionMode.DETACHED_70_30:
        try:
            expected_qtys = _split_qty_by_ratios(qty, [0.7, 0.3])
        except ValueError:
            return None
        if take_profit_qtys != expected_qtys:
            return None
        if tp_reprice_on_fill:
            tp_reprice_on_fill = False
    return BreakoutRunConfig(
        symbol=symbol.strip().upper(),
        qty=qty,
        rule=BreakoutRuleConfig(level=level, fast_entry=FastEntryConfig(enabled=fast_enabled)),
        entry_type=entry_type,
        take_profit=take_profit,
        take_profits=take_profits,
        take_profit_qtys=take_profit_qtys,
        stop_loss=stop_loss,
        use_rth=use_rth,
        bar_size=bar_size,
        fast_bar_size=fast_bar_size,
        max_bars=max_bars,
        tif=tif,
        outside_rth=outside_rth,
        account=account,
        client_tag=client_tag,
        quote_max_age_seconds=quote_max_age_seconds,
        tp_reprice_on_fill=tp_reprice_on_fill,
        tp_reprice_bar_size=tp_reprice_bar_size,
        tp_reprice_use_rth=tp_reprice_use_rth,
        tp_reprice_timeout_seconds=tp_reprice_timeout_seconds,
        ladder_execution_mode=ladder_execution_mode,
    )


def _parse_port(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"Invalid port value: {value}. Using {default}.")
        return default


async def _wait_for_port(host: str, port: int, *, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.5)
    return False


def _config_get(config: dict[str, str], key: str) -> Optional[str]:
    return config.get(key)


def _parse_symbol_tokens(
    *,
    args: list[str],
    symbol_value: Optional[str],
    default_symbol: Optional[str],
) -> list[str]:
    raw_tokens: list[str] = list(args)
    if symbol_value:
        raw_tokens.extend(symbol_value.split(","))
    if not raw_tokens and default_symbol:
        raw_tokens.append(default_symbol)

    symbols: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        for raw_symbol in token.split(","):
            symbol = raw_symbol.strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _is_trade_blocking_warning(message: str) -> bool:
    normalized = message.strip().lower()
    return any(term in normalized for term in _TRADE_BLOCKING_WARNING_TERMS)


def _find_trade_blocking_gateway_reject(
    messages: list[tuple[Optional[int], Optional[int], Optional[str], Optional[str]]],
    *,
    expected_req_id: Optional[int],
) -> Optional[str]:
    if expected_req_id is not None:
        matched = _find_trade_blocking_gateway_reject_for_req(
            messages, req_id_filter=expected_req_id
        )
        if matched:
            return matched
    return _find_trade_blocking_gateway_reject_for_req(messages, req_id_filter=None)


def _find_trade_blocking_gateway_reject_for_req(
    messages: list[tuple[Optional[int], Optional[int], Optional[str], Optional[str]]],
    *,
    req_id_filter: Optional[int],
) -> Optional[str]:
    for req_id, code, message, advanced in reversed(messages):
        if req_id_filter is not None and req_id != req_id_filter:
            continue
        text = (message or "").strip()
        if not text:
            text = (advanced or "").strip()
        if not text:
            continue
        normalized = text.lower()
        if code != 201 and "order rejected" not in normalized and not _is_trade_blocking_warning(text):
            continue
        code_label = f"ib code {int(code)}" if code is not None else "ib reject"
        req_label = f" req={req_id}" if req_id is not None and req_id >= 0 else ""
        return f"{code_label}{req_label}: {text}"
    return None


def _flag_aliases(command: str) -> dict[str, str]:
    if command in {"buy", "sell"}:
        return {
            "q": "qty",
            "l": "limit",
            "t": "tif",
            "o": "outside_rth",
            "a": "account",
            "c": "client_tag",
            "s": "symbol",
        }
    if command == "can-trade":
        return {
            "s": "symbol",
            "q": "qty",
            "t": "tif",
            "o": "outside_rth",
            "e": "exchange",
            "c": "currency",
            "a": "account",
        }
    if command == "connect":
        return {
            "h": "host",
            "p": "port",
            "c": "client_id",
            "r": "readonly",
            "t": "timeout",
        }
    if command == "breakout":
        return {
            "s": "symbol",
            "l": "level",
            "q": "qty",
            "p": "tp",
            "x": "sl",
            "r": "rth",
            "b": "bar",
            "m": "max_bars",
            "t": "tif",
            "o": "outside_rth",
            "e": "entry",
            "a": "account",
            "c": "client_tag",
        }
    if command == "orders":
        return {
            "p": "pending",
            "q": "qty",
            "l": "limit",
            "t": "tif",
            "o": "outside_rth",
            "a": "account",
            "s": "scope",
        }
    if command == "positions":
        return {"a": "account"}
    return {}


def _normalize_key(key: str, aliases: dict[str, str]) -> str:
    normalized = key.strip().lstrip("-").lower().replace("-", "_")
    return aliases.get(normalized, normalized)


def _parse_long_flag(
    token: str,
    tokens: list[str],
    idx: int,
) -> tuple[Optional[str], str, int]:
    key_value = token[2:]
    if not key_value:
        return None, "", 1
    if "=" in key_value:
        key, value = key_value.split("=", 1)
        return key, value, 1
    if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-"):
        return key_value, tokens[idx + 1], 2
    return key_value, "true", 1


def _parse_short_flag(
    token: str,
    tokens: list[str],
    idx: int,
    aliases: dict[str, str],
    kwargs: dict[str, str],
) -> int:
    body = token[1:]
    if not body:
        return 1
    if "=" in body:
        key, value = body.split("=", 1)
        kwargs[_normalize_key(key, aliases)] = value
        return 1
    if len(body) == 1:
        if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-"):
            kwargs[_normalize_key(body, aliases)] = tokens[idx + 1]
            return 2
        kwargs[_normalize_key(body, aliases)] = "true"
        return 1
    for key in body:
        kwargs[_normalize_key(key, aliases)] = "true"
    return 1


def _is_pending_only(args: list[str], kwargs: dict[str, str]) -> bool:
    if "pending" in kwargs:
        return _parse_bool(kwargs["pending"])
    for arg in args:
        if arg.lower() in {"pending", "--pending"}:
            return True
    return False


def _format_positions_table(
    positions: list[PositionSnapshot],
    *,
    tag_lookup: Optional[Callable[[Optional[str], str], Optional[str]]] = None,
    exit_lookup: Optional[Callable[[Optional[str], str], tuple[Optional[float], Optional[float]]]] = None,
    take_profits_lookup: Optional[Callable[[Optional[str], str], Optional[list[float]]]] = None,
) -> list[str]:
    headers = [
        "account",
        "symbol",
        "type",
        "qty",
        "avg_cost",
    ]
    if exit_lookup:
        headers.extend(["tp", "sl"])
    if tag_lookup:
        headers.append("tag")
    rows: list[list[str]] = []
    for pos in sorted(positions, key=lambda item: (item.account, item.symbol, item.sec_type)):
        tag = tag_lookup(pos.account, pos.symbol) if tag_lookup else None
        tp_value = None
        sl_value = None
        if exit_lookup:
            tp_value, sl_value = exit_lookup(pos.account, pos.symbol)
            tp_levels = take_profits_lookup(pos.account, pos.symbol) if take_profits_lookup else None
        row = [
            pos.account or "-",
            pos.symbol or "-",
            pos.sec_type or "-",
            _format_number(pos.qty),
            _format_number(pos.avg_cost),
        ]
        if exit_lookup:
            row.extend([_format_tp_display(tp_levels, tp_value), _format_number(sl_value)])
        if tag_lookup:
            row.append(tag or "-")
        rows.append(row)
    widths = [len(label) for label in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    header = " | ".join(label.ljust(widths[idx]) for idx, label in enumerate(headers))
    divider = "-+-".join("-" * width for width in widths)
    lines = [header, divider]
    for row in rows:
        lines.append(" | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))
    return lines


def _format_active_orders_table(orders: list[ActiveOrderSnapshot]) -> list[str]:
    headers = [
        "id",
        "parent",
        "symbol",
        "side",
        "type",
        "qty",
        "filled",
        "remaining",
        "limit",
        "stop",
        "status",
        "account",
        "tag",
    ]
    rows: list[list[str]] = []
    for order in sorted(
        orders,
        key=lambda item: (
            item.account or "",
            item.symbol or "",
            item.order_id if item.order_id is not None else -1,
        ),
    ):
        rows.append(
            [
                _format_int(order.order_id),
                _format_int(order.parent_order_id),
                order.symbol or "-",
                order.side or "-",
                order.order_type or "-",
                _format_number(order.qty),
                _format_number(order.filled_qty),
                _format_number(order.remaining_qty),
                _format_number(order.limit_price),
                _format_number(order.stop_price),
                order.status or "-",
                order.account or "-",
                order.client_tag or "-",
            ]
        )
    return _format_simple_table(headers, rows)


def _format_int(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return str(value)


def _normalize_account_key(value: Optional[str]) -> str:
    if value is None:
        return ""
    return value.strip().rstrip(".")


def _format_number(value: Optional[float], *, precision: int = 4) -> str:
    if value is None:
        return "-"
    try:
        formatted = f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"
    formatted = formatted.rstrip("0").rstrip(".")
    return formatted if formatted else "0"


def _format_tp_display(levels: Optional[list[float]], fallback: Optional[float]) -> str:
    if levels:
        return "[" + ",".join(_format_number(item) for item in levels) + "]"
    return _format_number(fallback)


def _format_breakout_levels(levels: list[float]) -> str:
    if not levels:
        return "-"
    return "[" + ",".join(_format_number(item) for item in levels) + "]"


def _is_breakout_client_tag(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower().startswith("breakout:")


def _maybe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delta_from_cumulative(
    cumulative_qty: Optional[float],
    *,
    order_id: Optional[int],
    snapshots_by_order_id: dict[int, float],
) -> Optional[float]:
    if cumulative_qty is None:
        return None
    normalized_qty = max(cumulative_qty, 0.0)
    if order_id is None:
        return normalized_qty
    previous_qty = snapshots_by_order_id.get(order_id)
    snapshots_by_order_id[order_id] = normalized_qty
    if previous_qty is None:
        return normalized_qty
    delta = normalized_qty - previous_qty
    if delta >= 0:
        return delta
    # Broker can occasionally reset cumulative snapshots; treat the new snapshot as current delta.
    return normalized_qty


def _is_tp_kind(kind: str) -> bool:
    normalized = kind.strip().lower()
    return (
        normalized.startswith("take_profit")
        or normalized.startswith("detached_tp_")
        or normalized.startswith("det70_tp_")
    )


def _is_stop_kind(kind: str) -> bool:
    normalized = kind.strip().lower()
    return (
        normalized in {"stop_loss", "detached_stop", "det70_stop"}
        or normalized.startswith("stop_loss_")
        or normalized.startswith("detached_stop_")
        or normalized.startswith("det70_stop_")
        or normalized == "det70_emergency_stop"
    )


def _append_breakout_issue(session: _BreakoutTradesSession, issue: str) -> None:
    if not issue:
        return
    if issue in session.issues:
        return
    session.issues.append(issue)


def _breakout_leg_alias(kind: str) -> str:
    normalized = kind.strip().lower()
    if normalized == "take_profit":
        return "tp1"
    if normalized.startswith("take_profit_"):
        suffix = normalized.split("_")[-1]
        return f"tp{suffix}"
    if normalized.startswith("detached_tp_"):
        suffix = normalized.split("_")[-1]
        return f"tp{suffix}"
    if normalized.startswith("det70_tp_"):
        suffix = normalized.split("_")[-1]
        return f"tp{suffix}"
    if normalized == "det70_emergency_stop":
        return "sl_emergency"
    if normalized in {"stop_loss", "detached_stop", "det70_stop"}:
        return "sl1"
    if normalized.startswith("stop_loss_"):
        suffix = normalized.split("_")[-1]
        return f"sl{suffix}"
    if normalized.startswith("detached_stop_"):
        suffix = normalized.split("_")[-1]
        return f"sl{suffix}"
    if normalized.startswith("det70_stop_"):
        suffix = normalized.split("_")[-1]
        return f"sl{suffix}"
    return normalized or "-"


def _breakout_legs_summary(session: _BreakoutTradesSession) -> str:
    tp_expected = sum(session.tp_expected_by_kind.values()) if session.tp_expected_by_kind else None
    tp_text = _format_number(session.tp_filled_qty if session.tp_filled_qty > 0 else 0.0)
    if tp_expected is not None:
        tp_text = f"{tp_text}/{_format_number(tp_expected)}"
    stop_text = _format_number(session.stop_filled_qty if session.stop_filled_qty > 0 else 0.0)
    parts = [f"tp={tp_text}", f"sl={stop_text}"]
    status_parts: list[str] = []
    for kind in sorted(session.child_status_by_kind):
        if not (_is_tp_kind(kind) or _is_stop_kind(kind)):
            continue
        alias = _breakout_leg_alias(kind)
        status = session.child_status_by_kind.get(kind) or "-"
        status_parts.append(f"{alias}:{status}")
    if status_parts:
        parts.append(f"[{','.join(status_parts)}]")
    return " ".join(parts)


def _breakout_protection_summary(session: _BreakoutTradesSession) -> str:
    if not session.protection_state:
        return "-"
    if not session.protection_reason:
        return session.protection_state
    return f"{session.protection_state}:{session.protection_reason}"


def _session_entry_is_fully_filled(session: _BreakoutTradesSession) -> bool:
    if session.entry_filled_qty <= 0:
        return False
    if session.entry_remaining_qty is not None:
        return session.entry_remaining_qty <= 1e-9
    if session.planned_entry_qty is not None and session.planned_entry_qty > 0:
        return session.entry_filled_qty + 1e-9 >= session.planned_entry_qty
    normalized_status = str(session.entry_last_status or "").strip().lower()
    return normalized_status == "filled"


def _breakout_outcome_kind(session: _BreakoutTradesSession) -> str:
    entry_qty = session.entry_filled_qty
    tp_qty = session.tp_filled_qty
    stop_qty = session.stop_filled_qty
    if entry_qty > 0 and stop_qty + 1e-9 >= entry_qty:
        return "stop"
    if entry_qty > 0 and tp_qty + 1e-9 >= entry_qty:
        return "tp"
    if tp_qty > 0 and stop_qty > 0:
        return "mixed"
    if tp_qty > 0:
        return "tp_partial"
    if stop_qty > 0:
        return "stop_partial"
    return "-"


def _match_prefix(text: str, options: list[str]) -> list[str]:
    if not options:
        return []
    if not text:
        return sorted(set(options))
    return sorted({option for option in options if option.startswith(text)})


def _print_exception(prefix: str, exc: BaseException) -> None:
    error_type = type(exc).__name__
    message = str(exc).splitlines()[0].strip()
    if len(message) > 200:
        message = message[:197].rstrip() + "..."
    if message:
        summary = f"{error_type}: {message}"
    else:
        summary = error_type
    ops_path = _resolve_ops_log_path()
    if ops_path:
        summary = f"{summary} (see {ops_path})"
    print(f"{prefix}: {summary}")


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _resolve_event_log_path() -> Optional[str]:
    log_path = os.getenv("APPS_EVENT_LOG_PATH")
    if log_path is None:
        log_path = os.getenv("APPV2_EVENT_LOG_PATH", "apps/journal/events.jsonl")
    return log_path or None


def _resolve_ops_log_path() -> Optional[str]:
    log_path = os.getenv("APPS_OPS_LOG_PATH", "apps/journal/ops.jsonl")
    return log_path or None


def _resolve_breakout_state_path() -> Optional[str]:
    log_path = os.getenv("APPS_BREAKOUT_STATE_PATH", "apps/journal/breakout_state.json")
    return log_path or None


def _parse_jsonl_timestamp(value: object, local_tz) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz)
    return parsed


def _is_fill_event(status: object, filled_qty: object) -> bool:
    qty = _maybe_float(filled_qty)
    if qty is not None and qty > 0:
        return True
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}


def _maybe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coalesce_number(primary: object, fallback: object) -> Optional[float]:
    value = _maybe_float(primary)
    if value is not None:
        return value
    return _maybe_float(fallback)


def _compute_pnl(
    entry_side: object,
    entry_price: object,
    exit_price: object,
    qty: object,
) -> Optional[float]:
    entry = _maybe_float(entry_price)
    exit_val = _maybe_float(exit_price)
    qty_val = _maybe_float(qty)
    if entry is None or exit_val is None or qty_val is None:
        return None
    side = str(entry_side or "").strip().upper()
    sign = 1.0 if side == "BUY" else -1.0
    return (exit_val - entry) * qty_val * sign


def _format_simple_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    widths = [len(label) for label in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    header = " | ".join(label.ljust(widths[idx]) for idx, label in enumerate(headers))
    divider = "-+-".join("-" * width for width in widths)
    lines = [header, divider]
    for row in rows:
        lines.append(" | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))
    return lines


def _format_kind(kind: object) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized == "take_profit":
        return "tp"
    if normalized == "stop_loss":
        return "sl"
    if normalized:
        return normalized
    return "-"


def _format_time_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S.%f")[:-4]
    return "-"
