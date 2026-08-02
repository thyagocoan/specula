"""User-configurable trading settings (fees, capital, position size).

Stored in data/meta/settings.json (survives container rebuilds via the bind
mount). New backtests, walk-forwards, curves and paper trades read these;
historical registry rows keep the fee they were run with (recorded in their
params).
"""

import json
from pathlib import Path

PATH = Path("data/meta/settings.json")

DEFAULTS = {
    # user's venues: Binance for crypto (spot taker), IBKR Pro tiered for
    # US stocks ($0.35 min on $1k trades = 0.035%/side)
    "fee_crypto_pct": 0.10,   # per side, percent (0.10 = 0.10%)
    "fee_stock_pct": 0.035,
    "capital_usd": 100_000.0,
    "trade_size_crypto_usd": 100.0,   # 0 = full capital per trade
    "trade_size_stock_usd": 1000.0,
}


def get_settings() -> dict:
    s = dict(DEFAULTS)
    if PATH.exists():
        try:
            s.update({k: float(v) for k, v in
                      json.loads(PATH.read_text(encoding="utf-8")).items()
                      if k in DEFAULTS})
        except Exception:
            pass
    return s


def save_settings(updates: dict) -> dict:
    s = get_settings()
    for k, v in updates.items():
        if k in DEFAULTS and v is not None:
            s[k] = float(v)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s
