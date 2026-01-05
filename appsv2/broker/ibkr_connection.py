from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ib_insync import IB


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
    def __init__(self, config: IBKRConnectionConfig, ib: Optional[IB] = None) -> None:
        self._config = config
        self._ib = ib or IB()

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

        await self._ib.connectAsync(
            new_host,
            new_port,
            clientId=new_client_id,
            timeout=new_timeout,
            readonly=new_readonly,
        )

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
        return self._config

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def status(self) -> dict[str, object]:
        return {
            "connected": self._ib.isConnected(),
            "host": self._config.host,
            "port": self._config.port,
            "client_id": self._config.client_id,
            "readonly": self._config.readonly,
            "paper_only": self._config.paper_only,
        }
