"""Live watcher: poll the option chain on an interval and stream snapshots."""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from .client import NiftyTraderClient, OptionChainError
from .models import OptionChain

SnapshotCallback = Callable[[OptionChain], None]
ErrorCallback = Callable[[Exception], None]


class LiveWatcher:
    """Continuously pull live option chain snapshots on an interval.

    Runs in its own thread; delivers each fresh snapshot to callbacks. The
    latest snapshot is always available via .latest. Stop with .stop().

    Example:
        w = LiveWatcher("NIFTY", interval=2.0)
        w.add_listener(lambda chain: print(chain.spot, chain.atm_row().calls.ltp))
        w.start()
        ...
        w.stop()
    """

    def __init__(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        interval: float = 2.0,
        client: Optional[NiftyTraderClient] = None,
    ):
        self.symbol = symbol.upper()
        self.expiry = expiry
        self.interval = max(0.5, float(interval))
        self.client = client or NiftyTraderClient()
        self._listeners: List[SnapshotCallback] = []
        self._error_handlers: List[ErrorCallback] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.latest: Optional[OptionChain] = None
        self.snapshot_count = 0
        self.last_error: Optional[str] = None

    def add_listener(self, cb: SnapshotCallback) -> None:
        self._listeners.append(cb)

    def on_error(self, cb: ErrorCallback) -> None:
        self._error_handlers.append(cb)

    def start(self) -> "LiveWatcher":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"option-chain-watch-{self.symbol}", daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                chain = self.client.get_chain(self.symbol, self.expiry)
                self.latest = chain
                self.snapshot_count += 1
                self.last_error = None
                for cb in list(self._listeners):
                    try:
                        cb(chain)
                    except Exception:
                        pass
            except OptionChainError as e:
                self.last_error = str(e)
                for cb in list(self._error_handlers):
                    try:
                        cb(e)
                    except Exception:
                        pass
            self._stop.wait(self.interval)
