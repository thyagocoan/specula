"""Signal library for the strategy lab.

Uniform interface: `generate(entry_spec, symbol, setup_tf, exec_tf)` returns
`(long_entries, short_entries, price_hint)` on the exec index — all look-ahead
safe. Setup-TF events become visible only at the setup bar's close (via
mtf.activation_positions); exec-TF signals use only completed exec bars.

entry_spec examples:
    {"kind": "ma_cross", "ma_type": "ema", "fast": 9, "slow": 21}
    {"kind": "orb", "range_min": 30}
    {"kind": "vwap", "mode": "revert", "band_k": 1.5}
    {"kind": "rsi_cross", "window": 14, "lo": 30, "hi": 70}
    {"kind": "didi", "tol_bars": 1, "adx_filter": false}
    {"kind": "fffd", "dev": 2.0, "strict": true}
"""

import numpy as np
import pandas as pd
from numba import njit

from specula import mtf
from specula.data import is_equity
from specula.features import wilder_rsi
from specula.indicators import bollinger


def _events_to_exec(event_index: pd.DatetimeIndex, setup_tf: str,
                    exec_index: pd.DatetimeIndex) -> pd.Series:
    """Setup-bar events -> one-bar True at their exec activation bar."""
    arr = np.zeros(len(exec_index), dtype=bool)
    if len(event_index):
        pos = mtf.activation_positions(event_index, setup_tf, exec_index)
        pos = pos[pos < len(exec_index)]
        arr[pos] = True
    return pd.Series(arr, index=exec_index)


def _day_key(exec_index: pd.DatetimeIndex, symbol: str) -> pd.Index:
    tz = "America/New_York" if is_equity(symbol) else "UTC"
    return pd.Index(exec_index.tz_convert(tz).date)


# ------------------------------------------------------------------ ma_cross

def ma_cross(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
             setup_tf: str, ma_type: str, fast: int, slow: int):
    close = setup_df["close"]
    if ma_type == "ema":
        f = close.ewm(span=fast, adjust=False).mean()
        s = close.ewm(span=slow, adjust=False).mean()
    else:
        f = close.rolling(fast).mean()
        s = close.rolling(slow).mean()
    up = (f > s) & (f.shift(1) <= s.shift(1))
    dn = (f < s) & (f.shift(1) >= s.shift(1))
    long_e = _events_to_exec(close.index[up.fillna(False)], setup_tf, exec_index)
    short_e = _events_to_exec(close.index[dn.fillna(False)], setup_tf, exec_index)
    return long_e, short_e, None


# ----------------------------------------------------------------------- orb

@njit(cache=True)
def _orb_machine(day_id, in_range, open_, high, low, n):
    long_e = np.zeros(n, np.bool_)
    short_e = np.zeros(n, np.bool_)
    price = np.full(n, np.nan)
    cur = -1
    rhi = 0.0
    rlo = 0.0
    have_range = False
    fired = False
    for i in range(n):
        if day_id[i] != cur:
            cur = day_id[i]
            rhi, rlo = -1e308, 1e308
            have_range = False
            fired = False
        if in_range[i]:
            if high[i] > rhi:
                rhi = high[i]
            if low[i] < rlo:
                rlo = low[i]
            have_range = True
            continue
        if not have_range or fired:
            continue
        if high[i] > rhi:
            long_e[i] = True
            price[i] = open_[i] if open_[i] > rhi else rhi
            fired = True
        elif low[i] < rlo:
            short_e[i] = True
            price[i] = open_[i] if open_[i] < rlo else rlo
            fired = True
    return long_e, short_e, price


def orb(exec_df: pd.DataFrame, symbol: str, range_min: int):
    """Opening-range breakout: first break of the first `range_min` minutes'
    range, one trade per session day, stop-order fill semantics."""
    idx = exec_df.index
    day = _day_key(idx, symbol)
    day_id, _ = pd.factorize(day)
    day_start = pd.Series(idx, index=idx).groupby(day_id).transform("min")
    minutes_in = np.asarray((idx - pd.DatetimeIndex(day_start)).total_seconds()) // 60
    in_range = np.asarray(minutes_in < range_min)
    long_e, short_e, price = _orb_machine(
        day_id.astype(np.int64), in_range,
        exec_df["open"].to_numpy(np.float64),
        exec_df["high"].to_numpy(np.float64),
        exec_df["low"].to_numpy(np.float64),
        len(exec_df),
    )
    return (pd.Series(long_e, index=idx), pd.Series(short_e, index=idx),
            pd.Series(price, index=idx))


# ---------------------------------------------------------------------- vwap

def vwap(exec_df: pd.DataFrame, symbol: str, mode: str, band_k: float = 1.5,
         warmup_bars: int = 15):
    """Session-anchored VWAP. `revert`: fade band touches back toward VWAP;
    `cross`: trend entries on VWAP crosses. Uses only completed bars."""
    idx = exec_df.index
    close, vol = exec_df["close"], exec_df["volume"]
    day = pd.Series(pd.factorize(_day_key(idx, symbol))[0], index=idx)
    g = day
    pv = (close * vol).groupby(g).cumsum()
    vv = vol.groupby(g).cumsum().replace(0, np.nan)
    vw = pv / vv
    n = g.groupby(g).cumcount() + 1
    dev = close - vw
    s1 = dev.groupby(g).cumsum()
    s2 = (dev ** 2).groupby(g).cumsum()
    var = (s2 / n - (s1 / n) ** 2).clip(lower=0)
    band = band_k * np.sqrt(var)
    ok = n >= warmup_bars
    lower, upper = vw - band, vw + band

    if mode == "revert":
        long_e = ok & (close > lower) & (close.shift(1) <= lower.shift(1))
        short_e = ok & (close < upper) & (close.shift(1) >= upper.shift(1))
    else:  # cross (trend)
        long_e = ok & (close > vw) & (close.shift(1) <= vw.shift(1))
        short_e = ok & (close < vw) & (close.shift(1) >= vw.shift(1))
    same_day = g.eq(g.shift(1))
    long_e = (long_e & same_day).fillna(False)
    short_e = (short_e & same_day).fillna(False)
    return long_e, short_e, None


# ------------------------------------------------------------------ fffd_ff

def fffd_ff(setup_df: pd.DataFrame, exec_df: pd.DataFrame, symbol: str,
            setup_tf: str, dev: float = 2.0, wait_bars: int = 3,
            vol_mult: float = 0.0, window: int = 20):
    """Anticipated FFFD: arm on the setup-TF candle that CLOSES OUTSIDE the
    band ("fechou fora") — optionally only on elevated volume — then watch
    the first `wait_bars` exec bars after its close. If none violates the FF
    candle's extreme, enter at the close of that window (fading the
    exhaustion before the classic fechou-dentro confirmation); a violation
    cancels the arm. Longs mirror on the lower band.

    Returns (long_e, short_e, price_hint=None, sl_hint) where sl_hint is the
    per-entry structural distance entry→FF-extreme, usable as a trailing
    stop distance (exit kind "trail", sl "structural").
    """
    close = setup_df["close"]
    lower, mid, upper = bollinger(close, window, dev)
    if vol_mult > 0:
        vol = setup_df["volume"]
        vol_ok = (vol >= vol_mult * vol.rolling(window).mean()).fillna(False)
    else:
        vol_ok = pd.Series(True, index=setup_df.index)
    ff_short = ((close > upper).fillna(False) & vol_ok).to_numpy()
    ff_long = ((close < lower).fillna(False) & vol_ok).to_numpy()

    idx = exec_df.index
    n = len(idx)
    high = exec_df["high"].to_numpy()
    low = exec_df["low"].to_numpy()
    ex_close = exec_df["close"].to_numpy()
    day = pd.factorize(_day_key(idx, symbol))[0]

    long_e = np.zeros(n, bool)
    short_e = np.zeros(n, bool)
    sl = np.full(n, np.nan)

    def scan(mask, extreme, is_short):
        ts = setup_df.index[mask]
        acts = mtf.activation_positions(ts, setup_tf, idx)
        for a, ext in zip(acts, extreme[mask]):
            p = a + wait_bars - 1
            if a >= n or p >= n or day[a] != day[p]:
                continue  # window must complete inside one session
            if is_short:
                if high[a:p + 1].max() > ext:
                    continue  # FF high taken out — arm invalidated
                short_e[p] = True
                dist = (ext - ex_close[p]) / ex_close[p]
            else:
                if low[a:p + 1].min() < ext:
                    continue
                long_e[p] = True
                dist = (ex_close[p] - ext) / ex_close[p]
            sl[p] = max(dist, 0.001)

    scan(ff_short, setup_df["high"].to_numpy(), True)
    scan(ff_long, setup_df["low"].to_numpy(), False)
    return (pd.Series(long_e, index=idx), pd.Series(short_e, index=idx),
            None, pd.Series(sl, index=idx))


# ------------------------------------------------------- explorer families

def donchian(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
             setup_tf: str, window: int):
    """Donchian channel breakout: close crosses the prior N-bar extreme."""
    close, high, low = setup_df["close"], setup_df["high"], setup_df["low"]
    hh = high.rolling(window).max().shift(1)
    ll = low.rolling(window).min().shift(1)
    above = close > hh
    below = close < ll
    up = (above & ~above.shift(1).fillna(False)).fillna(False)
    dn = (below & ~below.shift(1).fillna(False)).fillna(False)
    long_e = _events_to_exec(close.index[up], setup_tf, exec_index)
    short_e = _events_to_exec(close.index[dn], setup_tf, exec_index)
    return long_e, short_e, None


def boll(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
         setup_tf: str, window: int, dev: float, mode: str):
    """Bollinger band events. trend: break out of a band; revert: come back
    inside after a band excursion (fade)."""
    close = setup_df["close"]
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    upper, lower = mid + dev * sd, mid - dev * sd
    above, below = close > upper, close < lower
    if mode == "trend":
        up = (above & ~above.shift(1).fillna(False)).fillna(False)
        dn = (below & ~below.shift(1).fillna(False)).fillna(False)
    else:  # revert
        up = (~below & below.shift(1).fillna(False)).fillna(False)
        dn = (~above & above.shift(1).fillna(False)).fillna(False)
    long_e = _events_to_exec(close.index[up], setup_tf, exec_index)
    short_e = _events_to_exec(close.index[dn], setup_tf, exec_index)
    return long_e, short_e, None


def macd(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
         setup_tf: str, fast: int, slow: int, signal: int):
    close = setup_df["close"]
    line = (close.ewm(span=fast, adjust=False).mean()
            - close.ewm(span=slow, adjust=False).mean())
    sig_line = line.ewm(span=signal, adjust=False).mean()
    up = ((line > sig_line) & (line.shift(1) <= sig_line.shift(1))).fillna(False)
    dn = ((line < sig_line) & (line.shift(1) >= sig_line.shift(1))).fillna(False)
    long_e = _events_to_exec(close.index[up], setup_tf, exec_index)
    short_e = _events_to_exec(close.index[dn], setup_tf, exec_index)
    return long_e, short_e, None


def mom(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
        setup_tf: str, window: int, thr: float):
    """Momentum burst: N-bar rate of change crossing ±thr."""
    roc = setup_df["close"].pct_change(window)
    up = ((roc > thr) & (roc.shift(1) <= thr)).fillna(False)
    dn = ((roc < -thr) & (roc.shift(1) >= -thr)).fillna(False)
    long_e = _events_to_exec(roc.index[up], setup_tf, exec_index)
    short_e = _events_to_exec(roc.index[dn], setup_tf, exec_index)
    return long_e, short_e, None


# ----------------------------------------------------------------- rsi_cross

def rsi_cross(setup_df: pd.DataFrame, exec_index: pd.DatetimeIndex,
              setup_tf: str, window: int, lo: float, hi: float):
    r = wilder_rsi(setup_df["close"], window)
    up = (r > lo) & (r.shift(1) <= lo)      # leaving oversold -> long
    dn = (r < hi) & (r.shift(1) >= hi)      # leaving overbought -> short
    long_e = _events_to_exec(r.index[up.fillna(False)], setup_tf, exec_index)
    short_e = _events_to_exec(r.index[dn.fillna(False)], setup_tf, exec_index)
    return long_e, short_e, None


# ------------------------------------------------------------------ adapters

def didi(setup_df: pd.DataFrame, exec_df: pd.DataFrame, setup_tf: str,
         tol_bars: int = 1, adx_filter: bool = False):
    sig, _, _ = mtf.didi_setup_signals(setup_df, tol_bars=tol_bars,
                                       adx_filter=adx_filter)
    long_e, short_e, price, _ = mtf.run_breakout(sig, setup_tf, exec_df)
    idx = exec_df.index
    return (pd.Series(long_e, index=idx), pd.Series(short_e, index=idx),
            pd.Series(price, index=idx))


def fffd(setup_df: pd.DataFrame, exec_df: pd.DataFrame, setup_tf: str,
         dev: float = 2.0, strict: bool = True):
    sig, _, _, _ = mtf.fffd_setup_signals(setup_df, dev=dev, strict=strict)
    long_e, short_e, price, _ = mtf.run_breakout(sig, setup_tf, exec_df)
    idx = exec_df.index
    return (pd.Series(long_e, index=idx), pd.Series(short_e, index=idx),
            pd.Series(price, index=idx))


# ------------------------------------------------------------------ dispatch

def generate(entry: dict, symbol: str, setup_tf: str, exec_tf: str):
    from specula.backtest import frames

    setup_df = frames(symbol, setup_tf)
    exec_df = frames(symbol, exec_tf)
    kind = entry["kind"]
    if kind == "ma_cross":
        return ma_cross(setup_df, exec_df.index, setup_tf,
                        entry.get("ma_type", "sma"), entry["fast"], entry["slow"])
    if kind == "orb":
        return orb(exec_df, symbol, entry["range_min"])
    if kind == "vwap":
        return vwap(exec_df, symbol, entry["mode"], entry.get("band_k", 1.5))
    if kind == "rsi_cross":
        return rsi_cross(setup_df, exec_df.index, setup_tf,
                         entry.get("window", 14), entry.get("lo", 30),
                         entry.get("hi", 70))
    if kind == "donchian":
        return donchian(setup_df, exec_df.index, setup_tf, entry["window"])
    if kind == "boll":
        return boll(setup_df, exec_df.index, setup_tf,
                    entry.get("window", 20), entry.get("dev", 2.0),
                    entry.get("mode", "trend"))
    if kind == "macd":
        return macd(setup_df, exec_df.index, setup_tf,
                    entry.get("fast", 12), entry.get("slow", 26),
                    entry.get("signal", 9))
    if kind == "mom":
        return mom(setup_df, exec_df.index, setup_tf,
                   entry.get("window", 10), entry.get("thr", 0.01))
    if kind == "fffd_ff":
        return fffd_ff(setup_df, exec_df, symbol, setup_tf,
                       entry.get("dev", 2.0), entry.get("wait_bars", 3),
                       entry.get("vol_mult", 0.0))
    if kind == "didi":
        return didi(setup_df, exec_df, setup_tf,
                    entry.get("tol_bars", 1), entry.get("adx_filter", False))
    if kind == "fffd":
        return fffd(setup_df, exec_df, setup_tf,
                    entry.get("dev", 2.0), entry.get("strict", True))
    raise ValueError(f"unknown entry kind {kind}")
