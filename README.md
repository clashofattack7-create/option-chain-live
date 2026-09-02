# option-chain-live

Live **NSE option chain data** — LTP, Open Interest, volumes, IV, and Greeks for
every strike — for indices (NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY) and all
F&O stocks (RELIANCE, SBIN, …), without any API key or brokerage login.

## Why

Most option-chain sources want an API key, a broker login, or a paid
subscription. This package polls the **public JSON endpoint** behind
niftytrader.in (the same data its own website uses) and parses it into typed
structures:

- **No API key** · **No broker login** · **No websocket**
- Full chain or compact "5 around ATM" view
- Expiries, live dashboard (spot / PCR / max pain / OI), strike lists
- Continuous streaming with a threaded `LiveWatcher` or a simple CLI `watch`
- Console script and Python API

> ⚠️ **Data quality** — this is an *unofficial* public endpoint, not a
> broker-grade feed. It may change or rate-limit without notice. Poll at a sane
> interval (≥ 1 s) and treat values as **indicative** — verify before trading
> on them.
>
> Outside NSE trading hours the endpoint returns the last session's values.

## Install

```bash
pip install -e .
```

Requires Python ≥ 3.9 and `requests` (installed automatically).

## CLI

```bash
# One snapshot: nearest expiry, 5 strikes around ATM
option-chain chain NIFTY

# Specific expiry / more strikes / machine-readable output
option-chain chain BANKNIFTY --expiry 2026-09-29 --atm 8
option-chain chain FINNIFTY --json

# Stream every 2 s until Ctrl+C (or bound with --seconds)
option-chain watch NIFTY --interval 2
option-chain watch RELIANCE --interval 5 --seconds 60

# Expiries (nearest first), live market dashboard, strikes
option-chain expiries NIFTY
option-chain dashboard NIFTY
option-chain strikes BANKNIFTY
```

If the console script isn't on your PATH, use the module form:

```bash
python -m option_chain_live chain NIFTY
```

## Python API

```python
from option_chain_live import NiftyTraderClient, LiveWatcher

c = NiftyTraderClient()

chain = c.get_chain("NIFTY")          # nearest expiry, full chain
print(chain.spot, chain.expiry)
atm = chain.atm_row()
print(atm.strike, atm.calls.ltp, atm.puts.oi)
print(chain.max_pain_estimate)        # strike with max combined pain

c.get_expiries("BANKNIFTY")           # ["2026-09-01", ...] nearest first
c.get_dashboard("NIFTY")              # spot/PCR/max-pain/OI summaries
c.get_strikes("RELIANCE")             # available strikes
```

Continuous live stream in a background thread:

```python
w = LiveWatcher("NIFTY", interval=2)
w.add_listener(lambda ch: print(ch.fetched_at, ch.spot, ch.atm_row().calls.ltp))
w.start()
# ...
w.stop()
```

## Data per row

Each strike row carries paired **CE** and **PE** contracts with: `ltp`,
`net_change`, `oi`, `change_oi`, `change_oi_per`, `volume`, `iv`,
`bid_price`, `ask_price`, `open/high/low`, `oi_value`, `avg_price`,
Greeks (`delta`, `gamma`, `vega`, `theta`, `rho`), `intrinsic`,
`time_value`, `builtup`; plus row-level `pcr`, `spot` (`index_close`) and
`moneyness` (ATM/ITM/OTM). Chain-level `totals` hold ITM/OTM call-put buckets
and the aggregate OI/volume.

## Project layout

```
option-chain-live/
├── pyproject.toml                 # package + `option-chain` console script
├── README.md
└── option_chain_live/
    ├── __init__.py                # public exports
    ├── client.py                  # NiftyTraderClient (expiries/chain/dashboard/strikes)
    ├── models.py                  # OptionChain, ChainRow, ContractSide, totals, summaries
    ├── watch.py                   # LiveWatcher (background polling thread)
    ├── cli.py                     # chain | watch | expiries | dashboard | strikes
    └── __main__.py                # python -m option_chain_live
```

## Disclaimer

For research and educational purposes only. Not investment advice. The author is
not affiliated with NSE, niftytrader.in, or any broker. Market data may be
delayed or inaccurate — always confirm with your own sources.

## License

MIT — see [LICENSE](LICENSE).
