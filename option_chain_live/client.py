"""Thin client for the free NiftyTrader public JSON API (keyless)."""

from __future__ import annotations

import time
from typing import List, Optional

import requests

from .models import (
    ChainRow,
    ChainTotals,
    ContractSide,
    OptionChain,
    SideTotals,
    UnderlyingSummary,
)

# Public, keyless endpoints used by niftytrader.in's own web app.
API_BASE = "https://api.niftytrader.in/api/"
WEBAPI_BASE = "https://webapi.niftytrader.in/webapi/"

CHAIN_ENDPOINT = API_BASE + "option/option-chain-data"
EXPIRIES_ENDPOINT = WEBAPI_BASE + "Symbol/delta-symbol-expiry-list"
DASHBOARD_ENDPOINT = WEBAPI_BASE + "Option/dashboard-data"
STRIKES_ENDPOINT = WEBAPI_BASE + "Symbol/symbol-strike-price-list"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "platform_type": "2",
}


class OptionChainError(RuntimeError):
    """Raised when the upstream API rejects or fails a request."""


def _side_totals(raw, puts: bool = False) -> SideTotals:
    """Flatten one nested opTotals bucket (call or put side)."""
    if not isinstance(raw, dict):
        return SideTotals()
    def g(key: str) -> float:
        return float(raw.get(key) or 0)
    if puts:
        return SideTotals(
            oi=g("total_puts_oi"), change_oi=g("total_puts_change_oi"),
            volume=g("total_puts_volume"), oi_value=g("total_puts_oi_value"),
            change_oi_value=g("total_puts_change_oi_value"),
        )
    return SideTotals(
        oi=g("total_calls_oi"), change_oi=g("total_calls_change_oi"),
        volume=g("total_calls_volume"), oi_value=g("total_calls_oi_value"),
        change_oi_value=g("total_calls_change_oi_value"),
    )


class NiftyTraderClient:
    """Live NSE option-chain data client (no key, no login).

    The upstream is the public JSON API behind niftytrader.in. "Live" means
    each call returns the freshest snapshot available; use LiveWatcher for a
    continuous stream.
    """

    def __init__(self, timeout: float = 20.0, session: Optional[requests.Session] = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)

    def _get_json(self, url: str, params: dict) -> dict:
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise OptionChainError(f"network error for {url}: {e}") from e
        if r.status_code != 200:
            raise OptionChainError(f"HTTP {r.status_code} from {url}")
        try:
            payload = r.json()
        except ValueError as e:
            raise OptionChainError(f"non-JSON response from {url}: {r.text[:120]}") from e
        if not payload.get("result"):
            raise OptionChainError(
                f"upstream error: {payload.get('resultMessage', 'unknown')}"
            )
        return payload

    def get_expiries(self, symbol: str) -> List[str]:
        """ISO expiry dates (YYYY-MM-DD) for a symbol, nearest first."""
        payload = self._get_json(EXPIRIES_ENDPOINT, {"symbol": symbol.upper()})
        data = payload.get("resultData") or []
        dates = []
        for item in data:
            raw = item.get("expiry_date") or ""
            if raw:
                dates.append(raw[:10])
        dates = sorted(set(dates))
        today = time.strftime("%Y-%m-%d")
        upcoming = [d for d in dates if d >= today]
        return upcoming + [d for d in dates if d < today]

    def get_expiry(self, symbol: str, expiry: Optional[str] = None) -> str:
        """Resolve an expiry; defaults to the nearest upcoming one."""
        expiries = self.get_expiries(symbol)
        if not expiries:
            raise OptionChainError(f"no expiries returned for {symbol}")
        if expiry:
            exp = expiry[:10]
            if exp not in expiries:
                raise OptionChainError(
                    f"expiry {exp} not in {symbol} expiry list: {expiries[:5]} ..."
                )
            return exp
        return expiries[0]

    def get_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        """Full live option chain for a symbol (nearest expiry by default)."""
        symbol = symbol.upper()
        exp = self.get_expiry(symbol, expiry)
        payload = self._get_json(
            CHAIN_ENDPOINT, {"symbol": symbol, "expiry": exp}
        )
        rd = payload.get("resultData") or {}
        rows: List[ChainRow] = []
        spot = 0.0
        for item in rd.get("opDatas") or []:
            strike = float(item.get("strike_price") or 0)
            spot = float(item.get("index_close") or spot)
            calls = ContractSide(
                strike=strike, option_type="CE",
                ltp=float(item.get("calls_ltp") or 0),
                net_change=float(item.get("calls_net_change") or 0),
                oi=float(item.get("calls_oi") or 0),
                change_oi=float(item.get("calls_change_oi") or 0),
                change_oi_per=float(item.get("calls_change_oi_per") or 0),
                volume=float(item.get("calls_volume") or 0),
                iv=float(item.get("calls_iv") or 0),
                bid_price=float(item.get("calls_bid_price") or 0),
                ask_price=float(item.get("calls_ask_price") or 0),
                open=float(item.get("calls_open") or 0),
                high=float(item.get("calls_high") or 0),
                low=float(item.get("calls_low") or 0),
                oi_value=float(item.get("calls_oi_value") or 0),
                avg_price=float(item.get("calls_average_price") or 0),
                delta=float(item.get("call_delta") or 0),
                gamma=float(item.get("call_gamma") or 0),
                vega=float(item.get("call_vega") or 0),
                theta=float(item.get("call_theta") or 0),
                rho=float(item.get("call_rho") or 0),
                intrinsic=float(item.get("calls_intrisic") or 0),
                time_value=float(item.get("calls_time_value") or 0),
                builtup=item.get("calls_builtup") or "",
            )
            puts = ContractSide(
                strike=strike, option_type="PE",
                ltp=float(item.get("puts_ltp") or 0),
                net_change=float(item.get("puts_net_change") or 0),
                oi=float(item.get("puts_oi") or 0),
                change_oi=float(item.get("puts_change_oi") or 0),
                change_oi_per=float(item.get("puts_change_oi_per") or 0),
                volume=float(item.get("puts_volume") or 0),
                iv=float(item.get("puts_iv") or 0),
                bid_price=float(item.get("puts_bid_price") or 0),
                ask_price=float(item.get("puts_ask_price") or 0),
                open=float(item.get("puts_open") or 0),
                high=float(item.get("puts_high") or 0),
                low=float(item.get("puts_low") or 0),
                oi_value=float(item.get("puts_oi_value") or 0),
                avg_price=float(item.get("puts_average_price") or 0),
                delta=float(item.get("put_delta") or 0),
                gamma=float(item.get("put_gamma") or 0),
                vega=float(item.get("put_vega") or 0),
                theta=float(item.get("put_theta") or 0),
                rho=float(item.get("put_rho") or 0),
                intrinsic=float(item.get("puts_intrisic") or 0),
                time_value=float(item.get("puts_time_value") or 0),
                builtup=item.get("puts_builtup") or "",
            )
            rows.append(ChainRow(
                strike=strike, spot=spot, expiry=exp, symbol=symbol,
                pcr=float(item.get("pcr") or 0),
                time=item.get("time") or "",
                created_at=item.get("created_at"),
                calls=calls, puts=puts,
            ))
        rows.sort(key=lambda r: r.strike)
        totals_raw = rd.get("opTotals") or {}
        totals = ChainTotals(
            itm_calls=_side_totals(totals_raw.get("itm_total_calls")),
            itm_puts=_side_totals(totals_raw.get("itm_total_puts")),
            otm_calls=_side_totals(totals_raw.get("otm_total_calls")),
            otm_puts=_side_totals(totals_raw.get("otm_total_puts")),
            calls=_side_totals(totals_raw.get("total_calls_puts")),
            puts=_side_totals(totals_raw.get("total_calls_puts"), puts=True),
        )
        return OptionChain(symbol=symbol, expiry=exp, spot=spot, rows=rows, totals=totals)

    def get_dashboard(self, symbol: str) -> List[UnderlyingSummary]:
        """Live summary (spot, PCR, max pain, volumes) for index and stocks."""
        payload = self._get_json(DASHBOARD_ENDPOINT, {"symbol": symbol.upper()})
        rd = payload.get("resultData") or {}
        out = []
        for item in rd.get("indices") or []:
            out.append(self._summary(item))
        for item in rd.get("stocks") or []:
            out.append(self._summary(item))
        return out

    @staticmethod
    def _summary(item: dict) -> UnderlyingSummary:
        return UnderlyingSummary(
            symbol=item.get("symbol_name") or "",
            expiry=(item.get("expiry_date") or "")[:10],
            lot_size=int(item.get("lot_size") or 0),
            spot=float(item.get("last_trade_price") or item.get("index_close") or 0),
            change=float(item.get("change") or 0),
            change_percent=float(item.get("change_percent") or 0),
            max_pain_level=float(item.get("max_pain_level") or 0),
            total_calls_volume=float(item.get("total_calls_volume") or 0),
            total_puts_volume=float(item.get("total_puts_volume") or 0),
            total_calls_oi=float(item.get("total_calls_oi") or 0),
            total_puts_oi=float(item.get("total_puts_oi") or 0),
            total_oi=float(item.get("total_oi") or 0),
            pcr=float(item.get("pcr") or 0),
            option_volume=float(item.get("option_volume") or 0),
        )

    def get_strikes(self, symbol: str) -> List[float]:
        """Available strike prices for a symbol."""
        payload = self._get_json(STRIKES_ENDPOINT, {"symbol": symbol.upper()})
        return [float(x) for x in (payload.get("resultData") or [])]

    def wait_for_market(self, poll_seconds: float = 30.0, max_wait: float = 3600.0):
        """Block until the upstream starts returning fresh chain data."""
        waited = 0.0
        while waited < max_wait:
            try:
                chain = self.get_chain("NIFTY")
                if chain.rows and any(r.calls.oi or r.puts.oi for r in chain.rows):
                    return
            except OptionChainError:
                pass
            time.sleep(poll_seconds)
            waited += poll_seconds
