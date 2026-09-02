
"""
fyers_chain.py - map raw Fyers options-chain-v3 payloads to OptionChain models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import OptionChain, ChainRow, ContractSide, SideTotals, ChainTotals

_ROW_KEYS = ("strike", "ltp", "bid", "ask", "volume", "oi", "prev_oi",
             "iv", "delta", "gamma", "theta", "vega", "rho")


def _row_to_side(row: Any, strike: float, option_type: str) -> ContractSide:
    """Accept dict rows and index-array rows (strike/ltp/bid/ask/vol/oi heuristic)."""
    if isinstance(row, dict):
        g = lambda k: float(row.get(k) or 0.0)  # noqa: E731
        return ContractSide(
            strike=g("strike") or strike, option_type=option_type,
            ltp=g("ltp"), bid_price=g("bid"), ask_price=g("ask"),
            volume=g("volume"), oi=g("oi"), change_oi=g("prev_oi"),
            iv=g("iv") or 0.0,
            delta=g("delta"), gamma=g("gamma"), theta=g("theta"),
            vega=g("vega"), rho=g("rho"),
        )
    if isinstance(row, (list, tuple)) and row:
        vals = [float(x or 0) for x in row]
        strike = vals[0] or strike
        # heuristic index layout (documented): [strike, ltp, bid, ask, volume, oi, ...]
        def v(i: int) -> float:
            return vals[i] if i < len(vals) else 0.0
        return ContractSide(
            strike=strike, option_type=option_type,
            ltp=v(1), bid_price=v(2), ask_price=v(3),
            volume=v(4), oi=v(5), change_oi=v(6),
        )
    return ContractSide(strike=strike, option_type=option_type)


def _v3_side(row: dict, opt_type: str) -> ContractSide:
    """Map an options-chain-v3 (GET) row: flat row + nested greeks dict."""
    g = lambda k: float(row.get(k) or 0.0)  # noqa: E731
    gr = row.get("greeks") or {}
    iv_pct = float(gr.get("iv") or 0.0)
    return ContractSide(
        strike=g("strike_price"), option_type=opt_type,
        ltp=g("ltp"), bid_price=g("bid"), ask_price=g("ask"),
        volume=g("volume"), oi=g("oi"),
        change_oi=g("oi") - g("prev_oi"),
        change_oi_per=g("oichp"),
        iv=iv_pct / 100.0,  # API returns IV in percent
        delta=float(gr.get("delta") or 0.0),
        gamma=float(gr.get("gamma") or 0.0),
        theta=float(gr.get("theta") or 0.0),
        vega=float(gr.get("vega") or 0.0),
        rho=0.0,
    )


def _parse_v3(data: dict, symbol: str) -> OptionChain:
    """options-chain-v3 GET shape: data.optionsChain = flat per-side rows."""
    rows = data.get("optionsChain") or []
    spot = 0.0
    sides: Dict[float, Dict[str, ContractSide]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ot = row.get("option_type") or ""
        strike = float(row.get("strike_price") or 0)
        if ot == "":
            spot = float(row.get("ltp") or row.get("fp") or 0)
            continue
        if ot not in ("CE", "PE") or strike <= 0:
            continue
        sides.setdefault(strike, {})[ot] = _v3_side(row, ot)

    expiry = ""
    exp_list = data.get("expiryData") or []
    if exp_list and isinstance(exp_list[0], dict):
        d = str(exp_list[0].get("date") or "")
        parts = d.split("-")
        if len(parts) == 3:
            expiry = f"{parts[2]}-{parts[1]}-{parts[0]}"

    chain_rows: List[ChainRow] = []
    for strike in sorted(sides):
        pair = sides[strike]
        c = pair.get("CE")
        p = pair.get("PE")
        chain_rows.append(ChainRow(
            strike=strike, spot=spot, expiry=expiry, symbol=symbol,
            calls=c or ContractSide(strike, "CE"),
            puts=p or ContractSide(strike, "PE"),
        ))

    totals = ChainTotals(
        calls=SideTotals(oi=sum(r.calls.oi for r in chain_rows)),
        puts=SideTotals(oi=sum(r.puts.oi for r in chain_rows)),
    )
    return OptionChain(symbol=symbol, expiry=expiry, spot=spot,
                       rows=chain_rows, totals=totals,
                       fetched_at=datetime.now())


def parse_chain(raw: dict, symbol: str = "") -> OptionChain:
    """Build an OptionChain from the options-chain-v3 response."""
    data = raw.get("data") or {}
    if isinstance(data.get("optionsChain"), list):
        return _parse_v3(data, symbol)
    chain_data = data.get("chainData") or {}
    spot = float(data.get("last_traded_price") or data.get("ltp") or 0)
    expiry_desc = chain_data.get("expiry_desc") or data.get("expiry_desc") or ""
    expiry_ts = chain_data.get("expiresOn") or 0

    calls = chain_data.get("calls") or []
    puts = chain_data.get("puts") or []
    rows: List[ChainRow] = []
    for i in range(max(len(calls), len(puts))):
        c = calls[i] if i < len(calls) else None
        p = puts[i] if i < len(puts) else None
        strike = 0.0
        if isinstance(c, dict):
            strike = float(c.get("strike") or 0)
        elif isinstance(c, (list, tuple)):
            strike = float(c[0] or 0)
        elif isinstance(p, dict):
            strike = float(p.get("strike") or 0)
        elif isinstance(p, (list, tuple)):
            strike = float(p[0] or 0)
        site_c = _row_to_side(c, strike, "CE") if c is not None else None
        site_p = _row_to_side(p, strike, "PE") if p is not None else None
        if site_c is None and site_p is None:
            continue
        rows.append(ChainRow(
            strike=(site_c.strike if site_c else site_p.strike),
            spot=spot, expiry=expiry_desc[:10], symbol=symbol or "",
            calls=site_c or ContractSide(strike, "CE"),
            puts=site_p or ContractSide(strike, "PE"),
        ))
    rows.sort(key=lambda r: r.strike)
    ce_oi = sum(r.calls.oi for r in rows)
    pe_oi = sum(r.puts.oi for r in rows)
    totals = ChainTotals(
        calls=SideTotals(oi=ce_oi),
        puts=SideTotals(oi=pe_oi),
    )
    return OptionChain(symbol=symbol, expiry=expiry_desc[:10],
                       spot=spot, rows=rows, totals=totals,
                       fetched_at=datetime.now())
