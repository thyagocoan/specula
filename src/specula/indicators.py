"""Signal logic for the two strategies under test.

Rule sources: docs/research/strategy-rules.md (Didi Index / agulhada; Bollinger
"fechou fora, fechou dentro"). Ambiguities documented there are exposed here as
parameters so the sweep can explore them.
"""

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


# ---------------------------------------------------------------- Didi Index

def didi_lines(close: pd.Series, fast: int = 3, mid: int = 8, slow: int = 20):
    """Didi Index lines normalized so the mid average is a 0.0 baseline."""
    m = sma(close, mid)
    curta = sma(close, fast) / m - 1.0
    longa = sma(close, slow) / m - 1.0
    return curta, longa


def _cross_up(s: pd.Series) -> pd.Series:
    return (s > 0) & (s.shift(1) <= 0)


def _cross_dn(s: pd.Series) -> pd.Series:
    return (s < 0) & (s.shift(1) >= 0)


def agulhada_signals(close: pd.Series, tol_bars: int = 0,
                     fast: int = 3, mid: int = 8, slow: int = 20):
    """Agulhada: curta and longa cross the baseline in opposite directions
    within `tol_bars` of each other ("Queijo Minas" tolerance). Fires once, on
    the first bar where both crossings are in place and ordering holds."""
    curta, longa = didi_lines(close, fast, mid, slow)
    w = tol_bars + 1

    def within(sig: pd.Series) -> pd.Series:
        return sig.rolling(w, min_periods=1).max().astype(bool)

    alta = within(_cross_up(curta)) & within(_cross_dn(longa)) & (curta > 0) & (longa < 0)
    baixa = within(_cross_dn(curta)) & within(_cross_up(longa)) & (curta < 0) & (longa > 0)
    alta = alta & ~alta.shift(1, fill_value=False)
    baixa = baixa & ~baixa.shift(1, fill_value=False)
    return alta, baixa


def wilder_adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 8):
    """Classic Wilder DMI/ADX (RMA smoothing)."""
    up = high.diff()
    dn = -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    alpha = 1.0 / length
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx, pdi, mdi


def adx_confirmation(high, low, close, length: int = 8, threshold: float = 32.0):
    """Didi's trend filter: ADX rising and above threshold, DI in trade direction."""
    adx, pdi, mdi = wilder_adx(high, low, close, length)
    rising = adx.diff() > 0
    base = (adx > threshold) & rising
    long_ok = (base & (pdi > mdi)).fillna(False)
    short_ok = (base & (mdi > pdi)).fillna(False)
    return long_ok, short_ok


# ------------------------------------------------- Bollinger fechou fora/dentro

def bollinger(close: pd.Series, window: int = 20, dev: float = 2.0):
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std(ddof=0)
    return mid - dev * sd, mid, mid + dev * sd


def fffd_signals(close: pd.Series, window: int = 20, dev: float = 2.0,
                 strict: bool = False):
    """Fechou fora, fechou dentro (fade). Long: close below the lower band,
    then a close back inside. `strict` requires exactly one outside close
    (canonical form); loose fires on the first close back inside after >=1
    outside closes. Returns (long_signal, short_signal, lower, mid, upper)."""
    lower, mid, upper = bollinger(close, window, dev)
    below = (close < lower).fillna(False)
    above = (close > upper).fillna(False)
    long_sig = below.shift(1, fill_value=False) & ~below
    short_sig = above.shift(1, fill_value=False) & ~above
    if strict:
        long_sig &= ~below.shift(2, fill_value=False)
        short_sig &= ~above.shift(2, fill_value=False)
    return long_sig, short_sig, lower, mid, upper


def midband_cross_exits(close: pd.Series, mid: pd.Series):
    """Stormer's first target: close crossing the central band."""
    long_exit = (close > mid) & (close.shift(1) <= mid.shift(1))
    short_exit = (close < mid) & (close.shift(1) >= mid.shift(1))
    return long_exit.fillna(False), short_exit.fillna(False)
