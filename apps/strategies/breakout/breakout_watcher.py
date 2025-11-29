from typing import Optional

from loguru import logger

from apps.ib.client import IBClient
from apps.broker.service import BrokerService


def run_breakout_watcher(
    symbol: str,
    level: float,
    client: IBClient,
    service: BrokerService,
    *,
    qty: Optional[int] = None,
    use_rth: Optional[bool] = None,
    max_bars: Optional[int] = None,
) -> None:
    """
    Breakout watcher for the MVP.
    - Subscribes to 1-minute bars and applies a simple rule:
      break candle: first bar with high >= level
      confirm candle: next bar with open >= level
    - On confirmation, places a market BUY via BrokerService and exits (single-fire).
    """
    contract = client.qualify_stock(symbol)
    logger.info(f"[breakout watcher] subscribed to {symbol} level={level}")

    bars = client.ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="2 D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False if use_rth is None else use_rth,
        keepUpToDate=True,
    )

    last_count = len(bars)
    break_seen = False
    try:
        while True:
            client.ib.sleep(1.0)
            if len(bars) == last_count:
                continue

            new_bars = bars[last_count:]
            last_count = len(bars)
            for bar in new_bars:
                logger.info(
                    f"[breakout watcher] {symbol} bar: time={bar.date} o={bar.open} h={bar.high} l={bar.low} c={bar.close} v={bar.volume}"
                )

                if not break_seen:
                    if bar.high >= level:
                        break_seen = True
                        logger.info(f"[breakout watcher] break candle detected at {bar.date} (high={bar.high} >= {level})")
                    continue

                # We have seen a break; this is the next bar (confirm candidate)
                if bar.open >= level:
                    logger.info(f"[breakout watcher] confirm candle at {bar.date} (open={bar.open} >= {level}); sending market BUY")
                    buy_qty = qty if qty is not None else 0
                    if buy_qty <= 0:
                        logger.error("[breakout watcher] qty not set or zero; aborting trade")
                        return
                    try:
                        service.place_breakout_market_buy(symbol, buy_qty)
                    except Exception as exc:
                        logger.error(f"[breakout watcher] error placing order: {exc}")
                    return
                else:
                    logger.info(
                        f"[breakout watcher] confirm failed at {bar.date} (open={bar.open} < {level}); stopping without trade"
                    )
                    return

                if max_bars is not None and last_count >= max_bars:
                    logger.info("[breakout watcher] max_bars reached, stopping.")
                    return
    finally:
        try:
            client.ib.cancelHistoricalData(bars)
        except Exception as exc:
            logger.warning(f"[breakout watcher] cancel error: {exc}")
