from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from appsv2.adapters.broker.ibkr_connection import IBKRConnection

CommandHandler = Callable[[list[str], dict[str, str]], Awaitable[None]]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: CommandHandler
    help: str
    usage: str
    aliases: tuple[str, ...] = ()


class REPL:
    def __init__(self, connection: IBKRConnection, *, prompt: str = "appsv2> ") -> None:
        self._connection = connection
        self._prompt = prompt
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}
        self._should_exit = False
        self._register_commands()

    async def run(self) -> None:
        print("Appsv2 CLI (type 'help' to list commands).")
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
        args: list[str] = []
        kwargs: dict[str, str] = {}
        for token in tokens[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                kwargs[key.lower()] = value
            else:
                args.append(token)
        return cmd_name, args, kwargs

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
        self._should_exit = True


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
