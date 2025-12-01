import asyncio
import time
from typing import Optional

from ib_insync import AccountValue, Contract, IB, Order, Position, Stock, Ticker, Trade
from loguru import logger


class IBClient:
    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()

    def default_account(self, preferred: Optional[str] = None) -> Optional[str]:
        """Return preferred account if available, else first managed account."""
        accounts = list(self.ib.managedAccounts())
        if preferred and preferred in accounts:
            return preferred
        if accounts:
            return accounts[0]
        return preferred

    def connect(
        self,
        host: str,
        port: int,
        client_id: int,
        *,
        readonly: bool = False,
        timeout: float = 5,
    ) -> None:
        logger.info(f"Connecting IB {host}:{port} clientId={client_id} ...")
        self.ib.connect(host, port, clientId=client_id, readonly=readonly, timeout=timeout)
        logger.success("Connected.")

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def qualify_stock(self, symbol: str, exchange: str = "SMART", currency: str = "USD"):
        """Return a qualified IB contract for the given stock symbol."""
        contract = Stock(symbol, exchange, currency)
        [qualified] = self.ib.qualifyContracts(contract)
        return qualified

    async def qualify_stock_async(self, symbol: str, exchange: str = "SMART", currency: str = "USD"):
        """Async contract qualification to avoid blocking inside an active event loop."""
        contract = Stock(symbol, exchange, currency)
        contracts = await self.ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        return contracts[0]

    def get_reference_price(
        self,
        contract: Contract,
        *,
        timeout: float = 3.0,
        poll_interval: float = 0.1,
    ) -> float:
        """Fetch a recent price using last trade or bid/ask midpoint."""
        ticker: Ticker = self.ib.reqMktData(contract, "", False, False)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                self.ib.sleep(poll_interval)
                last, bid, ask = ticker.last, ticker.bid, ticker.ask
                if last and last > 0:
                    return float(last)
                if bid and ask and bid > 0 and ask > 0:
                    return float((bid + ask) / 2)
            raise RuntimeError("No reference price (check market data or symbol)")
        finally:
            self.ib.cancelMktData(contract)

    async def get_reference_price_async(self, contract: Contract) -> float:
        """
        Async snapshot price using reqTickersAsync with fallbacks:
        1) Real-time snapshot
        2) Delayed snapshot (if allowed)
        3) Last close via 1-day historical data
        """

        async def _try_snapshot() -> Optional[float]:
            tickers = await self.ib.reqTickersAsync(contract)
            if not tickers:
                return None
            ticker = tickers[0]
            last, bid, ask = ticker.last, ticker.bid, ticker.ask
            logger.debug(
                "Snapshot for %s: last=%s bid=%s ask=%s",
                getattr(contract, "symbol", ""),
                last,
                bid,
                ask,
            )
            if last and not (isinstance(last, float) and last != last):
                if last > 0:
                    return float(last)
            if bid and ask and bid > 0 and ask > 0:
                return float((bid + ask) / 2)
            return None

        # Try real-time snapshot first
        try:
            self.ib.reqMarketDataType(1)
        except Exception as exc:  # defensive; reqMarketDataType should be cheap
            logger.debug("reqMarketDataType(1) failed: %s", exc)
        price = await _try_snapshot()
        if price is not None:
            return price

        # Try delayed snapshot if permitted
        try:
            self.ib.reqMarketDataType(3)
            price = await _try_snapshot()
            if price is not None:
                return price
        except Exception as exc:
            logger.debug("Delayed market data snapshot failed: %s", exc)

        # Fall back to last close from a 1-day historical bar
        try:
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=1,
            )
            if bars:
                return float(bars[-1].close)
        except Exception as exc:
            logger.debug("Historical price fallback failed: %s", exc)

        raise RuntimeError("No reference price (check market data permissions or symbol)")

    def place_order(self, contract: Contract, order: Order) -> Trade:
        return self.ib.placeOrder(contract, order)

    def wait_for_order_id(
        self,
        trade: Trade,
        *,
        timeout: float = 2.0,
        poll_interval: float = 0.05,
    ) -> int:
        if trade.order.orderId:
            return trade.order.orderId
        deadline = time.time() + timeout
        while time.time() < deadline:
            if trade.order.orderId:
                return trade.order.orderId
            self.ib.sleep(poll_interval)
        if trade.order.orderId:
            return trade.order.orderId
        raise TimeoutError("Order ID not assigned")

    async def wait_for_order_id_async(
        self,
        trade: Trade,
        *,
        timeout: float = 2.0,
        poll_interval: float = 0.05,
    ) -> int:
        """Async wait for order id without blocking the running event loop."""
        if trade.order.orderId:
            return trade.order.orderId
        deadline = time.time() + timeout
        while time.time() < deadline:
            if trade.order.orderId:
                return trade.order.orderId
            await asyncio.sleep(poll_interval)
        if trade.order.orderId:
            return trade.order.orderId
        raise TimeoutError("Order ID not assigned")

    def wait_for_order_status(
        self,
        trade: Trade,
        *,
        timeout: float = 3.0,
        poll_interval: float = 0.1,
    ) -> Optional[str]:
        deadline = time.time() + timeout
        status = trade.orderStatus.status
        while not status and time.time() < deadline:
            self.ib.sleep(poll_interval)
            status = trade.orderStatus.status
        return status

    async def wait_for_order_status_async(
        self,
        trade: Trade,
        *,
        timeout: float = 3.0,
        poll_interval: float = 0.1,
    ) -> Optional[str]:
        """Async wait for order status without blocking the running event loop."""
        deadline = time.time() + timeout
        status = trade.orderStatus.status
        while not status and time.time() < deadline:
            await asyncio.sleep(poll_interval)
            status = trade.orderStatus.status
        return status

    async def get_positions_async(self, account: Optional[str] = None) -> list[Position]:
        """Async positions snapshot (optionally filtered by account)."""
        positions = await self.ib.reqPositionsAsync()
        if account is None:
            return positions
        return [p for p in positions if getattr(p, "account", None) == account]

    async def get_account_summary_async(
        self,
        account: Optional[str] = None,
        tags: str = "BuyingPower,AvailableFunds,NetLiquidation,TotalCashValue,UnrealizedPnL,RealizedPnL",
    ) -> dict[str, str]:
        """Async account summary filtered to a single account (if provided)."""
        values: list[AccountValue] = await self.ib.reqAccountSummaryAsync("All", tags)
        result: dict[str, str] = {}
        for val in values:
            if account is not None and val.account != account:
                continue
            result[val.tag] = f"{val.value} {val.currency}".strip()
        return result

    def cancel_all(self) -> int:
        trades = list(self.ib.openTrades())
        for tr in trades:
            try:
                self.ib.cancelOrder(tr.order)
            except Exception as exc:
                logger.error(f"Cancel error for orderId={tr.order.orderId}: {exc}")
        return len(trades)
