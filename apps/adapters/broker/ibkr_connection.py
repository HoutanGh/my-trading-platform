from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from ib_insync import IB

from apps.core.ops.events import (
    IbGatewayLog,
    IbkrConnectionAttempt,
    IbkrConnectionClosed,
    IbkrConnectionEstablished,
    IbkrConnectionFailed,
)

@dataclass
class IBKRConnectionConfig:
    host: str
    port: int
    client_id: int
    readonly: bool
    timeout: float
    paper_only: bool
    paper_port: int
    live_port: int

    @classmethod
    def from_env(cls) -> "IBKRConnectionConfig":
        return cls(
            host=os.getenv("IB_HOST", "127.0.0.1"),
            port=int(os.getenv("IB_PORT", "7497")),
            client_id=int(os.getenv("IB_CLIENT_ID", "1001")),
            readonly=os.getenv("IB_READONLY", "0") == "1",
            timeout=float(os.getenv("IB_TIMEOUT", "5")),
            paper_only=os.getenv("PAPER_ONLY", "1") == "1",
            paper_port=int(os.getenv("IB_PAPER_PORT", "7497")),
            live_port=int(os.getenv("IB_LIVE_PORT", "7496")),
        )


class IBKRConnection:
    def __init__(
        self,
        config: IBKRConnectionConfig,
        ib: Optional[IB] = None,
        *,
        gateway_logger: Optional[Callable[[object], None]] = None,
        event_logger: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._config = config
        self._ib = ib or IB()
        self._gateway_logger = gateway_logger
        self._event_logger = event_logger
        self._gateway_message_subscribers: list[
            Callable[[Optional[int], Optional[int], Optional[str], Optional[str]], None]
        ] = []
        self._install_error_filter()

    @property
    def ib(self) -> IB:
        return self._ib

    @property
    def config(self) -> IBKRConnectionConfig:
        return self._config

    def _assert_paper_mode(self, port: int) -> None:
        if self._config.paper_only and port != self._config.paper_port:
            raise RuntimeError("PAPER_ONLY=1 but IB port is not the paper port.")

    async def connect(
        self,
        *,
        mode: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        readonly: Optional[bool] = None,
        timeout: Optional[float] = None,
    ) -> IBKRConnectionConfig:
        new_host = host or self._config.host
        if mode == "paper":
            new_port = self._config.paper_port
        elif mode == "live":
            new_port = self._config.live_port
        else:
            new_port = port or self._config.port
        new_client_id = client_id if client_id is not None else self._config.client_id
        new_readonly = readonly if readonly is not None else self._config.readonly
        new_timeout = timeout if timeout is not None else self._config.timeout

        self._assert_paper_mode(new_port)
        if self._ib.isConnected():
            self._ib.disconnect()
            self._log_event(
                IbkrConnectionClosed.now(
                    host=self._config.host,
                    port=self._config.port,
                    client_id=self._config.client_id,
                    readonly=self._config.readonly,
                    reason="reconnect",
                )
            )

        self._log_event(
            IbkrConnectionAttempt.now(
                host=new_host,
                port=new_port,
                client_id=new_client_id,
                readonly=new_readonly,
                mode=mode,
            )
        )
        try:
            await self._ib.connectAsync(
                new_host,
                new_port,
                clientId=new_client_id,
                timeout=new_timeout,
                readonly=new_readonly,
            )
        except Exception as exc:
            self._log_event(
                IbkrConnectionFailed.now(
                    host=new_host,
                    port=new_port,
                    client_id=new_client_id,
                    readonly=new_readonly,
                    mode=mode,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            raise
        self._install_error_filter()

        self._config = IBKRConnectionConfig(
            host=new_host,
            port=new_port,
            client_id=new_client_id,
            readonly=new_readonly,
            timeout=new_timeout,
            paper_only=self._config.paper_only,
            paper_port=self._config.paper_port,
            live_port=self._config.live_port,
        )
        server_version = None
        try:
            server_version = int(self._ib.serverVersion())
        except Exception:
            server_version = None
        self._log_event(
            IbkrConnectionEstablished.now(
                host=new_host,
                port=new_port,
                client_id=new_client_id,
                readonly=new_readonly,
                server_version=server_version,
            )
        )
        return self._config

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()
            self._log_event(
                IbkrConnectionClosed.now(
                    host=self._config.host,
                    port=self._config.port,
                    client_id=self._config.client_id,
                    readonly=self._config.readonly,
                    reason="disconnect",
                )
            )

    def status(self) -> dict[str, object]:
        return {
            "connected": self._ib.isConnected(),
            "host": self._config.host,
            "port": self._config.port,
            "client_id": self._config.client_id,
            "readonly": self._config.readonly,
            "paper_only": self._config.paper_only,
        }

    def subscribe_gateway_messages(
        self,
        handler: Callable[[Optional[int], Optional[int], Optional[str], Optional[str]], None],
    ) -> Callable[[], None]:
        self._gateway_message_subscribers.append(handler)

        def _unsubscribe() -> None:
            if handler in self._gateway_message_subscribers:
                self._gateway_message_subscribers.remove(handler)

        return _unsubscribe

    def _install_error_filter(self) -> None:
        _silence_ib_insync_logger()
        wrappers = []
        wrapper = getattr(self._ib, "wrapper", None)
        if wrapper is not None:
            wrappers.append(wrapper)
        client = getattr(self._ib, "client", None)
        client_wrapper = getattr(client, "wrapper", None) if client else None
        if client_wrapper is not None and client_wrapper not in wrappers:
            wrappers.append(client_wrapper)

        for wrapper in wrappers:
            current_error = getattr(wrapper, "error", None)
            if not callable(current_error):
                continue
            if getattr(current_error, "_apps_filtered", False):
                continue

            def _filtered_error(*args, _original=current_error, **kwargs) -> None:
                payload = _parse_gateway_error(args, kwargs)
                if self._gateway_logger:
                    self._gateway_logger(
                        IbGatewayLog.now(
                            code=payload[1],
                            message=payload[2],
                            req_id=payload[0],
                            advanced=payload[3],
                            host=self._config.host,
                            port=self._config.port,
                            client_id=self._config.client_id,
                        )
                    )
                self._notify_gateway_message_subscribers(payload)
                if _should_suppress_error(args, kwargs):
                    return
                _original(*args, **kwargs)

            _filtered_error._apps_filtered = True  # type: ignore[attr-defined]
            wrapper.error = _filtered_error

    def _log_event(self, event: object) -> None:
        if self._event_logger:
            self._event_logger(event)

    def _notify_gateway_message_subscribers(
        self,
        payload: Tuple[Optional[int], Optional[int], Optional[str], Optional[str]],
    ) -> None:
        if not self._gateway_message_subscribers:
            return
        req_id, code, message, advanced = payload
        for handler in list(self._gateway_message_subscribers):
            try:
                handler(req_id, code, message, advanced)
            except Exception:
                continue


def _should_suppress_error(args: tuple[object, ...], kwargs: dict[str, object]) -> bool:
    error_code = None
    error_string = None
    if len(args) >= 3:
        error_code = args[1]
        error_string = args[2]
    else:
        error_code = kwargs.get("errorCode")
        error_string = kwargs.get("errorString") or kwargs.get("errorMsg")

    try:
        code = int(error_code) if error_code is not None else None
    except (TypeError, ValueError):
        code = None

    if code != 162:
        return False
    if not error_string:
        return True
    return "query cancelled" in str(error_string).lower()


def _silence_ib_insync_logger() -> None:
    logger = logging.getLogger("ib_insync")
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())


def _parse_gateway_error(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[str]]:
    req_id: Optional[int] = None
    error_code: Optional[int] = None
    error_msg: Optional[str] = None
    advanced: Optional[str] = None

    if len(args) >= 3:
        req_id = _maybe_int(args[0])
        error_code = _maybe_int(args[1])
        error_msg = str(args[2]) if args[2] is not None else None
        if len(args) >= 4:
            advanced = str(args[3]) if args[3] is not None else None
    else:
        req_id = _maybe_int(kwargs.get("reqId"))
        error_code = _maybe_int(kwargs.get("errorCode"))
        error_msg = (
            str(kwargs.get("errorString"))
            if kwargs.get("errorString") is not None
            else None
        )
        if error_msg is None and kwargs.get("errorMsg") is not None:
            error_msg = str(kwargs.get("errorMsg"))
        if kwargs.get("advancedOrderRejectJson") is not None:
            advanced = str(kwargs.get("advancedOrderRejectJson"))

    return req_id, error_code, error_msg, advanced


def _maybe_int(value: object) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
