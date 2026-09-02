"""Command line interface: option-chain chain|watch|expiries|dashboard|strikes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from .client import NiftyTraderClient, OptionChainError
from .models import OptionChain
from .watch import LiveWatcher

INTERVAL_CHOICES = [0.5, 1, 2, 3, 5, 10, 15, 30, 60]


def _fmt(v: float, w: int = 9) -> str:
    if v == 0:
        return "-".rjust(w)
    if abs(v) >= 1000:
        return f"{v:,.0f}".rjust(w)
    if abs(v) >= 100:
        return f"{v:,.1f}".rjust(w)
    return f"{v:,.2f}".rjust(w)


def _fmt_iv(v: float) -> str:
    return f"{v*100:.1f}%".rjust(7) if v else "-".rjust(7)


def print_chain_table(chain: OptionChain, around_atm: Optional[int] = None) -> None:
    rows = chain.nearest_rows(around_atm) if around_atm else chain.rows
    now = chain.fetched_at.strftime("%H:%M:%S")
    header = f"{chain.symbol}  expiry={chain.expiry}  spot={chain.spot:,.2f}  fetched {now}"
    print("=" * len(header))
    print(header)
    max_pain = chain.max_pain_estimate
    if max_pain is not None:
        print(
            f"max pain ~{max_pain:,.0f}  |  OI: calls {chain.totals.total_calls:,.0f} "
            f"puts {chain.totals.total_puts:,.0f}  |  "
            f"total {chain.totals.total_oi:,.0f}"
        )
    print("-" * len(header))
    print(
        f"{'CE LTP':>9} {'CE OI':>10} {'CE IV':>7} {'CE D':>6} | "
        f"{'STRIKE':>9} | "
        f"{'PE LTP':>9} {'PE OI':>10} {'PE IV':>7} {'PE D':>6}  {'MONEY'}"
    )
    for r in rows:
        marker = " *" if r.moneyness == "ATM" else "  "
        print(
            f"{_fmt(r.calls.ltp)} {_fmt(r.calls.oi, 10)} {_fmt_iv(r.calls.iv)} "
            f"{r.calls.delta:>6.2f} | {r.strike:>9,.0f} | "
            f"{_fmt(r.puts.ltp)} {_fmt(r.puts.oi, 10)} {_fmt_iv(r.puts.iv)} "
            f"{r.puts.delta:>6.2f}  {r.moneyness}{marker}"
        )


def cmd_chain(args) -> int:
    c = NiftyTraderClient()
    try:
        chain = c.get_chain(args.symbol, args.expiry)
    except OptionChainError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(chain.as_dict(), indent=2))
    else:
        print_chain_table(chain, around_atm=args.atm)
    return 0


def cmd_watch(args) -> int:
    c = NiftyTraderClient()
    w = LiveWatcher(args.symbol, args.expiry, interval=args.interval, client=c)
    first = [True]

    def on_snapshot(chain: OptionChain) -> None:
        if args.json:
            print(json.dumps(chain.as_dict()))
            sys.stdout.flush()
            return
        if first[0]:
            print_chain_table(chain, around_atm=args.atm)
            first[0] = False
        else:
            atm = chain.atm_row()
            print(
                f"[{chain.fetched_at.strftime('%H:%M:%S')}] spot {chain.spot:,.2f}  "
                f"ATM {atm.strike:,.0f}  CE {atm.calls.ltp:,.2f} "
                f"(d {atm.calls.delta:.2f}, OI {atm.calls.oi:,.0f})  "
                f"PE {atm.puts.ltp:,.2f} (d {atm.puts.delta:.2f}, OI {atm.puts.oi:,.0f})"
            )
        sys.stdout.flush()

    def on_err(e: Exception) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] error: {e}", file=sys.stderr)

    w.add_listener(on_snapshot)
    w.on_error(on_err)
    w.start()
    print(
        f"watching {args.symbol} every {args.interval}s (expiry: {args.expiry or 'auto'}) - Ctrl+C to stop",
        file=sys.stderr,
    )
    try:
        deadline = None
        if args.seconds is not None:
            deadline = time.monotonic() + args.seconds
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        w.stop()
    return 0


def cmd_expiries(args) -> int:
    c = NiftyTraderClient()
    try:
        expiries = c.get_expiries(args.symbol)
    except OptionChainError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(expiries, indent=2))
    else:
        for i, e in enumerate(expiries):
            tag = "  <- nearest" if i == 0 else ""
            print(e + tag)
    return 0


def cmd_dashboard(args) -> int:
    c = NiftyTraderClient()
    try:
        items = c.get_dashboard(args.symbol)
    except OptionChainError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([i.as_dict() for i in items], indent=2))
    else:
        for i in items:
            print(
                f"{i.symbol:<12} spot {i.spot:>10,.2f} ({i.change_percent:+.2f}%)  "
                f"exp {i.expiry}  lot {i.lot_size}  PCR {i.pcr:.2f}  "
                f"maxPain {i.max_pain_level:,.0f}  "
                f"CE OI {i.total_calls_oi:>12,.0f}  PE OI {i.total_puts_oi:>12,.0f}"
            )
    return 0


def cmd_strikes(args) -> int:
    c = NiftyTraderClient()
    try:
        strikes = c.get_strikes(args.symbol)
    except OptionChainError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(strikes, indent=2))
    else:
        print(", ".join(f"{s:,.0f}" for s in strikes))
    return 0



# ---- Fyers (official Trading API) commands ----

DEFAULT_FYERS_APP = "HU97KUI4I4-200"
DEFAULT_FYERS_REDIRECT = "https://trade.fyers.in/api-login/redirect-uri/index.html"


def _fyers_creds(args, token_file=None):
    import os
    from .fyers_client import FyersCredentials
    return FyersCredentials(
        app_id=args.app_id,
        secret=args.secret or os.environ.get("FYERS_SECRET", ""),
        client_id=args.client_id or os.environ.get("FYERS_CLIENT_ID", ""),
        redirect_uri=args.redirect_uri,
        token_file=token_file,
    )


def cmd_fyers_auth(args) -> int:
    """One-time interactive OAuth: browser login, then token cached."""
    import subprocess, sys, os
    creds = _fyers_creds(args)
    tool = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "fyers_browser.js",
    )
    if not os.path.exists(tool):
        print(f"error: missing {tool}", file=sys.stderr)
        return 1
    env = dict(os.environ)
    if args.client_id:
        env["FYERS_CLIENT_ID"] = args.client_id
    if args.pin:
        env["FYERS_PIN"] = args.pin
    print(
        f"opening browser for Fyers login (app {args.app_id}) -> {args.redirect_uri}",
        file=sys.stderr,
    )
    print("complete the login in the browser window (enter your 4-digit PIN)", file=sys.stderr)
    try:
        proc = subprocess.run(
            ["node", tool, "auth", args.app_id, args.secret, args.redirect_uri,
             str(args.timeout_min), creds.token_file],
            env=env, timeout=args.timeout_min * 60 + 60,
        )
    except subprocess.TimeoutExpired:
        print("error: login flow timed out", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("error: node not found on PATH", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        print("error: login flow failed - see Node output above", file=sys.stderr)
        return 1
    tokens = creds.load_tokens()
    if not tokens or not tokens.get("access_token"):
        print("error: no tokens saved", file=sys.stderr)
        return 1
    print(f"OK: token cached in {creds.token_file}")
    print(f"    expires {tokens.get('expires_at')}")
    print(f"    refresh token: {'yes' if tokens.get('refresh_token') else 'no'}")
    return 0


def cmd_fyers_chain(args) -> int:
    """Live option chain from the official Fyers API (with IV + Greeks)."""
    import json as _json, sys
    from .fyers_client import FyersClient, FyersError
    creds = _fyers_creds(args)
    c = FyersClient(creds)
    try:
        raw = c.option_chain_raw(args.symbol, strikecount=args.strike_count)
    except Exception as e:  # FyersError or auth
        print(f"error: {e}", file=sys.stderr)
        print("hint: run 'python -m option_chain_live fyers-auth' first", file=sys.stderr)
        return 1
    if args.json:
        print(_json.dumps(raw, indent=2, default=str))
        return 0
    from .fyers_chain import parse_chain
    chain = parse_chain(raw, args.symbol)
    print_chain_table(chain, around_atm=args.atm)
    return 0


def cmd_fyers_refresh(args) -> int:
    """Refresh the cached token (needs the 4-digit PIN)."""
    import sys
    from .fyers_client import FyersClient, FyersError
    creds = _fyers_creds(args)
    c = FyersClient(creds)
    try:
        c.refresh_token(pin=args.pin)
    except FyersError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("OK: token refreshed and cached")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="option-chain",
        description="Pull live NSE option chain data (free, keyless NiftyTrader API).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("chain", help="one live option chain snapshot")
    pc.add_argument("symbol", help="NIFTY, BANKNIFTY, FINNIFTY, or a F&O stock like RELIANCE")
    pc.add_argument("--expiry", default=None, help="YYYY-MM-DD (default: nearest)")
    pc.add_argument("--atm", type=int, default=5, help="show N strikes around ATM")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_chain)

    pw = sub.add_parser("watch", help="stream live snapshots until Ctrl+C")
    pw.add_argument("symbol", help="NIFTY, BANKNIFTY, FINNIFTY, or a F&O stock")
    pw.add_argument("--expiry", default=None, help="YYYY-MM-DD (default: nearest)")
    pw.add_argument("--interval", type=float, default=2.0, choices=INTERVAL_CHOICES,
                    help="seconds between pulls")
    pw.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    pw.add_argument("--atm", type=int, default=5)
    pw.add_argument("--json", action="store_true")
    pw.set_defaults(func=cmd_watch)

    pe = sub.add_parser("expiries", help="list available expiries (nearest first)")
    pe.add_argument("symbol")
    pe.add_argument("--json", action="store_true")
    pe.set_defaults(func=cmd_expiries)

    pd = sub.add_parser("dashboard", help="live underlyings summary (spot/PCR/max pain/OI)")
    pd.add_argument("symbol", nargs="?", default="NIFTY")
    pd.add_argument("--json", action="store_true")
    pd.set_defaults(func=cmd_dashboard)

    ps = sub.add_parser("strikes", help="list available strikes")
    ps.add_argument("symbol")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_strikes)


    # ---- Fyers (official API) ----
    pf = sub.add_parser("fyers-auth", help="one-time interactive Fyers login (browser OAuth)")
    pf.add_argument("--app-id", default=DEFAULT_FYERS_APP)
    pf.add_argument("--secret", default=None, help="Secret ID (default: from --app-id or env FYERS_SECRET)")
    pf.add_argument("--client-id", default=None, help="Fyers client id (auto-filled, e.g. YA38754)")
    pf.add_argument("--redirect-uri", default=DEFAULT_FYERS_REDIRECT)
    pf.add_argument("--pin", default=None, help="4-digit PIN (optional; otherwise type it in the browser)")
    pf.add_argument("--timeout-min", type=float, default=10.0)
    pf.set_defaults(func=cmd_fyers_auth)

    pfc = sub.add_parser("fyers-chain", help="live option chain from the official Fyers API (IV + Greeks)")
    pfc.add_argument("symbol", help="NSE symbol, e.g. NSE:NIFTY50-INDEX / NSE:BANKNIFTY-INDEX / NSE:RELIANCE-EQ")
    pfc.add_argument("--app-id", default=DEFAULT_FYERS_APP)
    pfc.add_argument("--secret", default=None)
    pfc.add_argument("--client-id", default=None)
    pfc.add_argument("--redirect-uri", default=DEFAULT_FYERS_REDIRECT)
    pfc.add_argument("--strike-count", type=int, default=20, help="strikes around ATM (x2 +1 rows)")
    pfc.add_argument("--atm", type=int, default=5)
    pfc.add_argument("--json", action="store_true")
    pfc.set_defaults(func=cmd_fyers_chain)

    pfr = sub.add_parser("fyers-refresh", help="refresh cached Fyers token (needs PIN)")
    pfr.add_argument("--app-id", default=DEFAULT_FYERS_APP)
    pfr.add_argument("--secret", default=None)
    pfr.add_argument("--client-id", default=None)
    pfr.add_argument("--redirect-uri", default=DEFAULT_FYERS_REDIRECT)
    pfr.add_argument("--pin", required=True, help="4-digit PIN")
    pfr.set_defaults(func=cmd_fyers_refresh)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
