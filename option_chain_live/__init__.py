"""option-chain-live — pull live NSE option chain data, keyless and free.

Data source: NiftyTrader public JSON API (https://www.niftytrader.in).
No API key, no brokerage login, no websocket needed. Polling-based "live".

Quick start:
    from option_chain_live.client import NiftyTraderClient
    c = NiftyTraderClient()
    chain = c.get_chain("NIFTY")            # nearest expiry, full chain
    print(chain.spot, chain.atm_row().calls.ltp)
"""

from .client import NiftyTraderClient
from .models import (
    ContractSide,
    ChainRow,
    ChainTotals,
    OptionChain,
    UnderlyingSummary,
)
from .watch import LiveWatcher

__version__ = "0.1.0"

__all__ = [
    "NiftyTraderClient",
    "ContractSide",
    "ChainRow",
    "ChainTotals",
    "OptionChain",
    "UnderlyingSummary",
    "LiveWatcher",
    "__version__",
]
