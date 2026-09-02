"""Typed models for the live option chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class ContractSide:
    """One side (CE or PE) of a strike row."""

    strike: float
    option_type: str  # "CE" | "PE"
    ltp: float = 0.0
    net_change: float = 0.0
    oi: float = 0.0
    change_oi: float = 0.0
    change_oi_per: float = 0.0
    volume: float = 0.0
    iv: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    oi_value: float = 0.0
    avg_price: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    intrinsic: float = 0.0
    time_value: float = 0.0
    builtup: str = ""

    @property
    def is_itm(self) -> bool:
        return self.intrinsic > 0


@dataclass
class ChainRow:
    """One strike row: paired CE and PE contracts plus shared fields."""

    strike: float
    spot: float
    expiry: str
    symbol: str
    pcr: float = 0.0
    time: str = ""
    created_at: Optional[str] = None
    calls: ContractSide = field(default_factory=lambda: ContractSide(0, "CE"))
    puts: ContractSide = field(default_factory=lambda: ContractSide(0, "PE"))

    @property
    def moneyness(self) -> str:
        """ATM / ITM / OTM relative to spot."""
        if self.spot and abs(self.spot - self.strike) / self.spot < 0.005:
            return "ATM"
        return "ITM" if self.strike < self.spot else "OTM"

    def as_dict(self) -> dict:
        return {
            "strike": self.strike,
            "spot": self.spot,
            "expiry": self.expiry,
            "moneyness": self.moneyness,
            "pcr": self.pcr,
            "calls": {k: getattr(self.calls, k) for k in (
                "ltp", "net_change", "oi", "change_oi", "change_oi_per", "volume",
                "iv", "bid_price", "ask_price", "delta", "gamma", "vega", "theta",
                "intrinsic", "time_value", "builtup")},
            "puts": {k: getattr(self.puts, k) for k in (
                "ltp", "net_change", "oi", "change_oi", "change_oi_per", "volume",
                "iv", "bid_price", "ask_price", "delta", "gamma", "vega", "theta",
                "intrinsic", "time_value", "builtup")},
        }


@dataclass
class SideTotals:
    """Aggregate numbers for one bucket (e.g. ITM calls)."""

    oi: float = 0.0
    change_oi: float = 0.0
    volume: float = 0.0
    oi_value: float = 0.0
    change_oi_value: float = 0.0


@dataclass
class ChainTotals:
    itm_calls: SideTotals = field(default_factory=SideTotals)
    itm_puts: SideTotals = field(default_factory=SideTotals)
    otm_calls: SideTotals = field(default_factory=SideTotals)
    otm_puts: SideTotals = field(default_factory=SideTotals)
    calls: SideTotals = field(default_factory=SideTotals)
    puts: SideTotals = field(default_factory=SideTotals)

    @property
    def total_calls(self) -> float:
        return self.calls.oi

    @property
    def total_puts(self) -> float:
        return self.puts.oi

    @property
    def total_oi(self) -> float:
        return self.calls.oi + self.puts.oi


@dataclass
class OptionChain:
    """A full option chain snapshot for one symbol + expiry."""

    symbol: str
    expiry: str
    spot: float = 0.0
    fetched_at: datetime = field(default_factory=datetime.now)
    rows: List[ChainRow] = field(default_factory=list)
    totals: ChainTotals = field(default_factory=ChainTotals)

    def atm_row(self) -> Optional[ChainRow]:
        """Row nearest to spot."""
        if not self.rows:
            return None
        return min(self.rows, key=lambda r: abs(r.strike - r.spot))

    def row_at(self, strike: float) -> Optional[ChainRow]:
        for r in self.rows:
            if abs(r.strike - strike) < 1e-6:
                return r
        return None

    def nearest_rows(self, n: int = 5) -> List[ChainRow]:
        """n rows around ATM (n below, n above, ATM inclusive)."""
        if not self.rows:
            return []
        atm = self.atm_row()
        if atm is None:
            return []
        idx = self.rows.index(atm)
        return self.rows[max(0, idx - n): idx + n + 1]

    @property
    def max_pain_estimate(self) -> Optional[float]:
        """Max pain: strike where combined (CE below + PE above) OI is smallest."""
        if not self.rows:
            return None
        best, best_val = None, None
        for r in self.rows:
            ce_oi = sum(x.calls.oi for x in self.rows if x.strike <= r.strike)
            pe_oi = sum(x.puts.oi for x in self.rows if x.strike >= r.strike)
            v = ce_oi + pe_oi
            if best_val is None or v < best_val:
                best, best_val = r.strike, v
        return best

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry,
            "spot": self.spot,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
            "max_pain_estimate": self.max_pain_estimate,
            "totals": {
                "total_calls_oi": self.totals.total_calls,
                "total_puts_oi": self.totals.total_puts,
                "total_oi": self.totals.total_oi,
            },
            "rows": [r.as_dict() for r in self.rows],
        }


@dataclass
class UnderlyingSummary:
    """Compact live snapshot from the dashboard endpoint."""

    symbol: str
    expiry: str
    lot_size: int
    spot: float
    change: float = 0.0
    change_percent: float = 0.0
    max_pain_level: float = 0.0
    total_calls_volume: float = 0.0
    total_puts_volume: float = 0.0
    total_calls_oi: float = 0.0
    total_puts_oi: float = 0.0
    total_oi: float = 0.0
    pcr: float = 0.0
    option_volume: float = 0.0
    fetched_at: datetime = field(default_factory=datetime.now)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["fetched_at"] = self.fetched_at.isoformat(timespec="seconds")
        return d
