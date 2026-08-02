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


# --------------------------------------------- higher-TF support/resistance

LEVEL_SPECS = [("sma", "1d", 200), ("sma", "1d", 50), ("ema", "4h", 21)]


def level_matrix(symbol: str, exec_tf: str) -> pd.DataFrame:
    """Distance (%) from close to higher-TF levels, aligned look-ahead safe.

    Columns: dist_{kind}{window}_{tf} (percent above/below the level),
    slope_{...} (+1 rising / -1 falling level), dist_pdh / dist_pdl
    (percent from the PRIOR completed day's high/low).
    """
    from specula.backtest import frames

    exec_df = frames(symbol, exec_tf)
    idx = exec_df.index
    close = exec_df["close"]
    cols = {}
    for kind, tf, w in LEVEL_SPECS:
        s = frames(symbol, tf)["close"]
        ma = (s.ewm(span=w, adjust=False).mean() if kind == "ema"
              else s.rolling(w).mean())
        aligned = mtf.map_to_exec(ma, tf, idx)
        cols[f"dist_{kind}{w}_{tf}"] = 100 * (close - aligned) / aligned
        cols[f"slope_{kind}{w}_{tf}"] = np.sign(
            mtf.map_to_exec(ma.diff(), tf, idx))
    daily = frames(symbol, "1d")
    # map_to_exec already delays a day's value until that bar closes, so
    # these are the prior completed day's extremes at any intraday moment
    pdh = mtf.map_to_exec(daily["high"], "1d", idx)
    pdl = mtf.map_to_exec(daily["low"], "1d", idx)
    cols["dist_pdh"] = 100 * (close - pdh) / pdh
    cols["dist_pdl"] = 100 * (close - pdl) / pdl
    return pd.DataFrame(cols, index=idx)


# ----------------------------------------------------------- regime gates

def _session_day(symbol: str, idx: pd.DatetimeIndex) -> pd.Index:
    from specula.data import is_equity

    tz = "America/New_York" if is_equity(symbol) else "UTC"
    return pd.Index(idx.tz_convert(tz).date)


def gap_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Qualify only days that OPEN with a gap ≥ min_gap_pct vs the prior
    close. mode "aligned": longs only on gap-ups, shorts on gap-downs."""
    from specula.backtest import frames

    ex = frames(symbol, exec_tf)
    idx = ex.index
    day = _session_day(symbol, idx)
    day_open = ex["open"].groupby(day).transform("first")
    prev_close = ex["close"].groupby(day).last().shift(1)
    prev = pd.Series(prev_close.reindex(day).to_numpy(), index=idx)
    gap = (day_open - prev) / prev
    big = (gap.abs() >= flt.get("min_gap_pct", 0.5) / 100).fillna(False)
    if flt.get("mode") == "aligned":
        return big & (gap > 0), big & (gap < 0)
    return big, big.copy()


def compression_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Qualify days whose PRIOR day's range was compressed (≤ q × the
    20-day average range) — the classic expansion-follows-compression day."""
    from specula.backtest import frames

    ex = frames(symbol, exec_tf)
    idx = ex.index
    day = _session_day(symbol, idx)
    rng = ex["high"].groupby(day).max() - ex["low"].groupby(day).min()
    ok_day = (rng <= flt.get("q", 0.8) * rng.rolling(20).mean()).shift(1)
    ok = pd.Series(ok_day.reindex(day).to_numpy(), index=idx)
    ok = ok.astype("boolean").fillna(False).astype(bool)
    return ok, ok.copy()


def trend_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Align with the daily trend: longs only above the daily SMA(ma),
    shorts only below — using the prior completed day's value."""
    from specula.backtest import frames

    ex = frames(symbol, exec_tf)
    idx = ex.index
    d = frames(symbol, "1d")["close"]
    sma = d.rolling(flt.get("ma", 20)).mean()
    above = mtf.map_to_exec(((d > sma).astype(float)), "1d", idx)
    return (above > 0.5).fillna(False), (above < 0.5).fillna(False)


def session_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Restrict entries to a session window: open60 (first hour), close90
    (last 90 min), not_midday (both, excluding the chop)."""
    from specula.data import is_equity

    from specula.backtest import frames

    idx = frames(symbol, exec_tf).index
    if is_equity(symbol):
        local = idx.tz_convert("America/New_York")
        mins = pd.Series(local.hour * 60 + local.minute - 570, index=idx)
        length = 390
    else:
        mins = pd.Series(idx.hour * 60 + idx.minute, index=idx)
        length = 1440
    w = flt.get("window", "open60")
    if w == "open60":
        ok = mins < (60 if length == 390 else 240)
    elif w == "close90":
        ok = mins >= length - (90 if length == 390 else 240)
    else:  # not_midday
        a = 90 if length == 390 else 360
        ok = (mins < a) | (mins >= length - a)
    return ok, ok.copy()


def regime_entry_mask(symbol: str, exec_tf: str, flt: dict):
    kind = flt.get("ind")
    fn = {"gap": gap_entry_mask, "compression": compression_entry_mask,
          "trend": trend_entry_mask, "session": session_entry_mask}[kind]
    return fn(symbol, exec_tf, flt)


def level_entry_mask(symbol: str, exec_tf: str, flt: dict):
    """Block entries inside specified level-distance bands.

        {"ind": "level",
         "block_long":  [{"col": "dist_ema21_4h", "lo": -1.0, "hi": 0.0}],
         "block_short": []}

    Returns (long_ok, short_ok) — True where entries are allowed.
    """
    lm = level_matrix(symbol, exec_tf)
    long_ok = pd.Series(True, index=lm.index)
    short_ok = pd.Series(True, index=lm.index)
    for spec in flt.get("block_long", []):
        band = lm[spec["col"]].between(spec["lo"], spec["hi"])
        long_ok &= ~band.fillna(False)
    for spec in flt.get("block_short", []):
        band = lm[spec["col"]].between(spec["lo"], spec["hi"])
        short_ok &= ~band.fillna(False)
    return long_ok, short_ok


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
