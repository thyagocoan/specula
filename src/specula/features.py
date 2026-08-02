"""Multi-timeframe feature snapshots for trade-quality analysis and filters.

Every feature is computed on its own timeframe and mapped onto the execution
index with the same look-ahead rule as the MTF engine: a bar's value becomes
visible only at that bar's close. A trade entered at 14:03 therefore sees
yesterday's daily RSI, the last completed 4h bar's RSI, and so on.
"""

import numpy as np
import pandas as pd

from specula import mtf

FEATURE_TFS = ["1d", "4h", "2h", "1h", "30min", "15min"]


def wilder_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def rsi_matrix(symbol: str, exec_tf: str, tfs: list[str] | None = None,
               window: int = 14) -> pd.DataFrame:
    """RSI per timeframe, aligned (look-ahead safe) to the exec index."""
    from specula.backtest import frames

    tfs = FEATURE_TFS if tfs is None else tfs
    exec_index = frames(symbol, exec_tf).index
    cols = {}
    for tf in tfs:
        rsi = wilder_rsi(frames(symbol, tf)["close"], window)
        cols[f"rsi_{tf}"] = mtf.map_to_exec(rsi, tf, exec_index)
    return pd.DataFrame(cols, index=exec_index)


def rsi_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Boolean masks (long_ok, short_ok) on the exec index from a filter spec:

        {"ind": "rsi", "tf": "1d", "window": 14,
         "long": [35, 100], "short": [0, 65]}          # inside-band allowed
        {"ind": "rsi", "tf": "1d", "mode": "outside",
         "long": [40, 60], "short": [40, 60]}          # only extremes allowed

    Default mode allows entries when RSI is INSIDE the band; mode="outside"
    allows entries only when RSI is outside it (fade-at-extremes logic).
    """
    from specula.backtest import frames

    window = flt.get("window", 14)
    rsi = wilder_rsi(frames(symbol, flt["tf"])["close"], window)
    exec_index = frames(symbol, exec_tf).index
    aligned = mtf.map_to_exec(rsi, flt["tf"], exec_index)
    lo_l, hi_l = flt.get("long", [0, 100])
    lo_s, hi_s = flt.get("short", [0, 100])
    if flt.get("mode") == "outside":
        long_ok = ((aligned < lo_l) | (aligned > hi_l)).fillna(False)
        short_ok = ((aligned < lo_s) | (aligned > hi_s)).fillna(False)
    else:
        long_ok = ((aligned >= lo_l) & (aligned <= hi_l)).fillna(False)
        short_ok = ((aligned >= lo_s) & (aligned <= hi_s)).fillna(False)
    return long_ok, short_ok
