from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.cli.order_tracker import OrderTracker
from apps.core.market_data.ports import BarStreamPort
from apps.core.orders.models import OrderSide, OrderSpec, OrderType
from apps.core.orders.ports import EventBus
from apps.core.orders.service import OrderService, OrderValidationError
from apps.core.pnl.service import PnlService
from apps.core.strategies.breakout.logic import BreakoutRuleConfig
from apps.core.strategies.breakout.runner import BreakoutRunConfig, run_breakout

CommandHandler = Callable[[list[str], dict[str, str]], Awaitable[None]]


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
        bar_stream: Optional[BarStreamPort] = None,
        event_bus: Optional[EventBus] = None,
        *,
        prompt: str = "apps> ",
    ) -> None:
        self._connection = connection
        self._order_service = order_service
        self._order_tracker = order_tracker
        self._pnl_service = pnl_service
        self._bar_stream = bar_stream
        self._event_bus = event_bus
        self._prompt = prompt
        self._config: dict[str, str] = {}
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}
        self._should_exit = False
        self._breakout_tasks: dict[str, tuple[BreakoutRunConfig, asyncio.Task]] = {}
        self._register_commands()

    async def run(self) -> None:
        print("Apps CLI (type 'help' to list commands).")
        while not self._should_exit:
            try:
                line = await asyncio.to_thread(input, self._prompt)
            except EOFError:
                print()
                break
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
            try:
                await spec.handler(args, kwargs)
            except Exception as exc:
                print(f"Error: {exc}")
        await self._stop_breakouts()

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
                usage="buy SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false] [account=...] [client_tag=...]",
            )
        )
        self._register(
            CommandSpec(
                name="sell",
                handler=self._cmd_sell,
                help="Submit a basic sell order (market or limit).",
                usage="sell SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false] [account=...] [client_tag=...]",
            )
        )
        self._register(
            CommandSpec(
                name="breakout",
                handler=self._cmd_breakout,
                help="Start or stop a breakout watcher.",
                usage=(
                    "breakout SYMBOL level=... qty=... [tp=...] [sl=...] [rth=true|false] [bar=1 min] "
                    "[max_bars=...] [tif=DAY] [outside_rth=true|false] [account=...] [client_tag=...] "
                    "| breakout status | breakout stop [SYMBOL]"
                ),
            )
        )
        self._register(
            CommandSpec(
                name="orders",
                handler=self._cmd_orders,
                help="Show tracked order statuses from events.",
                usage="orders [pending]",
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

    def _register(self, spec: CommandSpec) -> None:
        self._commands[spec.name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = spec.name

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
        print(
            "Connected: "
            f"{cfg.host}:{cfg.port} client_id={cfg.client_id} readonly={cfg.readonly}"
        )

    async def _cmd_buy(self, args: list[str], kwargs: dict[str, str]) -> None:
        await self._submit_order(OrderSide.BUY, args, kwargs)

    async def _cmd_sell(self, args: list[str], kwargs: dict[str, str]) -> None:
        await self._submit_order(OrderSide.SELL, args, kwargs)

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
            if action == "stop":
                symbol = args[1] if len(args) > 1 else kwargs.get("symbol")
                await self._stop_breakouts(symbol=symbol)
                return

        symbol = args[0] if args else kwargs.get("symbol") or _config_get(self._config, "symbol")
        if not symbol:
            print(self._commands["breakout"].usage)
            return
        level_raw = kwargs.get("level") or _config_get(self._config, "level")
        qty_raw = kwargs.get("qty") or _config_get(self._config, "qty")
        if level_raw is None or qty_raw is None:
            print(self._commands["breakout"].usage)
            return
        try:
            level = float(level_raw)
        except ValueError:
            print("level must be a number")
            return
        try:
            qty = int(qty_raw)
        except ValueError:
            print("qty must be an integer")
            return

        tp_raw = kwargs.get("tp") or _config_get(self._config, "tp")
        sl_raw = kwargs.get("sl") or _config_get(self._config, "sl")
        take_profit = None
        stop_loss = None
        if tp_raw is not None or sl_raw is not None:
            if tp_raw is None or sl_raw is None:
                print("tp and sl must be provided together")
                return
            try:
                take_profit = float(tp_raw)
            except ValueError:
                print("tp must be a number")
                return
            try:
                stop_loss = float(sl_raw)
            except ValueError:
                print("sl must be a number")
                return

        bar_size = (
            kwargs.get("bar")
            or kwargs.get("bar_size")
            or _config_get(self._config, "bar_size")
            or "1 min"
        )
        use_rth_value = kwargs.get("rth") or kwargs.get("use_rth") or _config_get(self._config, "use_rth")
        use_rth = _parse_bool(use_rth_value) if use_rth_value is not None else False
        outside_rth_value = kwargs.get("outside_rth") or _config_get(self._config, "outside_rth")
        outside_rth = _parse_bool(outside_rth_value) if outside_rth_value is not None else False
        tif = kwargs.get("tif") or _config_get(self._config, "tif") or "DAY"
        account = kwargs.get("account") or _config_get(self._config, "account")
        client_tag = kwargs.get("client_tag") or _config_get(self._config, "client_tag")

        max_bars_raw = kwargs.get("max_bars") or _config_get(self._config, "max_bars")
        max_bars = None
        if max_bars_raw is not None:
            try:
                max_bars = int(max_bars_raw)
            except ValueError:
                print("max_bars must be an integer")
                return

        symbol = symbol.strip().upper()
        task_name = f"breakout:{symbol}:{level}"
        if task_name in self._breakout_tasks:
            print(f"Breakout watcher already running: {task_name}")
            return

        run_config = BreakoutRunConfig(
            symbol=symbol,
            qty=qty,
            rule=BreakoutRuleConfig(level=level),
            take_profit=take_profit,
            stop_loss=stop_loss,
            use_rth=use_rth,
            bar_size=bar_size,
            max_bars=max_bars,
            tif=tif,
            outside_rth=outside_rth,
            account=account,
            client_tag=client_tag,
        )

        task = asyncio.create_task(
            run_breakout(
                run_config,
                bar_stream=self._bar_stream,
                order_service=self._order_service,
                event_bus=self._event_bus,
            ),
            name=task_name,
        )
        self._breakout_tasks[task_name] = (run_config, task)
        task.add_done_callback(lambda t: self._on_breakout_done(task_name, t))
        print(f"Breakout watcher started: {task_name}")

    async def _cmd_orders(self, _args: list[str], _kwargs: dict[str, str]) -> None:
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

    def _print_breakout_status(self) -> None:
        if not self._breakout_tasks:
            print("No breakout watchers running.")
            return
        for name, (config, task) in sorted(self._breakout_tasks.items()):
            state = "running" if not task.done() else "done"
            print(
                f"{name} symbol={config.symbol} level={config.rule.level} "
                f"qty={config.qty} state={state}"
            )

    async def _stop_breakouts(self, symbol: Optional[str] = None) -> None:
        if not self._breakout_tasks:
            return
        symbol_filter = symbol.strip().upper() if symbol else None
        targets = []
        for name, (config, task) in self._breakout_tasks.items():
            if symbol_filter and config.symbol != symbol_filter:
                continue
            targets.append((name, task))
        if not targets:
            if symbol_filter:
                print(f"No breakout watchers found for {symbol_filter}.")
            return
        for _name, task in targets:
            task.cancel()
        await asyncio.gather(*(task for _, task in targets), return_exceptions=True)
        for name, _task in targets:
            self._breakout_tasks.pop(name, None)
        print(f"Stopped {len(targets)} breakout watcher(s).")

    def _on_breakout_done(self, task_name: str, task: asyncio.Task) -> None:
        self._breakout_tasks.pop(task_name, None)
        if task.cancelled():
            print(f"Breakout watcher cancelled: {task_name}")
            return
        exc = task.exception()
        if exc:
            print(f"Breakout watcher failed: {task_name} error={exc}")
            return
        print(f"Breakout watcher finished: {task_name}")

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
        if not self._config:
            print("Config: (empty)")
            return
        for key in sorted(self._config):
            print(f"{key}={self._config[key]}")

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
        await self._stop_breakouts()
        self._should_exit = True

    async def _submit_order(
        self,
        side: OrderSide,
        args: list[str],
        kwargs: dict[str, str],
    ) -> None:
        if not self._order_service:
            print("Order service not configured.")
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
                "[outside_rth=true|false] [account=...] [client_tag=...]"
            )
            return
        qty_raw = kwargs.get("qty") or _config_get(self._config, "qty")
        if qty_raw is None:
            print(
                "Usage: buy|sell SYMBOL qty=... [limit=...] [tif=DAY] "
                "[outside_rth=true|false] [account=...] [client_tag=...]"
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


def _config_get(config: dict[str, str], key: str) -> Optional[str]:
    return config.get(key)


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
            "a": "account",
            "c": "client_tag",
        }
    if command == "orders":
        return {"p": "pending"}
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
