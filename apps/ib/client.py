from ib_insync import IB, util

_ib = None

def get_ib(host: str = '172.21.224.1', port: int = 7497, clientId: int = 509, timeout: float = 10.0) -> IB:
    """
    Return a singleton IB instance connected to TWS/Gateway.

    - Safe to call multiple times (idempotent).
    - Starts the nested event loop for notebooks.
    """
    global _ib
    # Ensure notebook-compatible asyncio loop (safe to call multiple times)
    util.startLoop()

    if _ib is None:
        _ib = IB()

    if not _ib.isConnected():
        _ib.connect(host, port=port, clientId=clientId, timeout=timeout)

    return _ib

def disconnect_ib() -> None:
    """Disconnect the singleton IB instance if connected."""
    global _ib
    if _ib is not None and _ib.isConnected():
        _ib.disconnect()
