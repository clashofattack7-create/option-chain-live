# option-chain-live

A keyless, login-free plugin that pulls **live NSE option chain data** — LTP, Open
Interest, volumes, IV, and Greeks for every strike, for indices
(NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY) and all F&O stocks (RELIANCE, SBIN, ...).

No API key. No broker login. No websocket. Data comes from the public JSON API
behind niftytrader.in (the same one its own website uses), polled on an interval
for "live" behavior.

> Note: unofficial public endpoint — may change or rate-limit without notice.
> Poll at a sane interval (>=1s) and treat data as indicative, not broker-grade.

## Install

```powershell
cd D:\dsh\DSH\option-chain-live
pip install -e .
```

Requires Python >=3.9 and `requests` (installed automatically).

## CLI

```powershell
# One live snapshot of the nearest expiry, 5 strikes around ATM
option-chain chain NIFTY

# A specific expiry / more or fewer strikes / machine-readable JSON
option-chain chain BANKNIFTY --expiry 2026-09-29 --atm 8
option-chain chain FINNIFTY --json

# Stream live updates every 2s until Ctrl+C (or for N seconds)
option-chain watch NIFTY --interval 2
option-chain watch RELIANCE --interval 5 --seconds 60

# Expiries for a symbol (nearest first), live market summary, strikes
option-chain expiries NIFTY
option-chain dashboard NIFTY
option-chain strikes BANKNIFTY
```

If the console script is not on your PATH, run the module form instead:
```powershell
python -m option_chain_live chain NIFTY
```

## Python API

```python
from option_chain_live import NiftyTraderClient, LiveWatcher

c = NiftyTraderClient()

chain = c.get_chain("NIFTY")                 # nearest expiry, full chain
print(chain.spot, chain.expiry)
atm = chain.atm_row()
print(atm.strike, atm.calls.ltp, atm.puts.oi)
print(chain.max_pain_estimate)               # strike with max combined pain

c.get_expiries("BANKNIFTY")                  # ["2026-09-01", ...] nearest first
c.get_dashboard("NIFTY")                     # spot/PCR/max-pain/OI summaries
c.get_strikes("RELIANCE")                    # available strikes

# Continuous live stream in a background thread:
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
`time_value`, `builtup`, plus row-level `pcr`, `spot` (`index_close`) and
`moneyness` (ATM/ITM/OTM). Chain-level `totals` hold ITM/OTM call-put buckets
and the aggregate OI/volume.

## Using it from DeepSeek Harness (DSH)

The plugin is a plain Python package, so any DSH session on this machine can use
it straight away:

- **One-shot snapshot** (agent tool call): `python -m option_chain_live chain NIFTY --json`
  — returns the whole chain as JSON the agent can reason over.
- **Live polling inside a session**: run
  `python -m option_chain_live watch NIFTY --interval 2 --seconds 30` as a
  background job and read its output; or import the package directly in a Python
  script and drive `LiveWatcher` with your own callbacks.
- **Keep it fresh**: add the package to the workspace and reference it in future
  prompts as "use the option-chain-live plugin".

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
