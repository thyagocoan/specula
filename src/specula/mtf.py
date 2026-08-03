"""Multi-timeframe execution: setup-timeframe signals, exec-timeframe fills.

Look-ahead rule: a setup bar labeled by its left edge is only knowable at its
close (left_edge + tf). Every signal/value coming from the setup timeframe is
therefore timestamped at bar close before being mapped onto the exec index, so
the exec layer can only act from the first exec bar at/after that close.
"""

import numpy as np
import pandas as pd
from numba import njit

from specula import indicators as ind


def tf_delta(tf: str) -> pd.Timedelta:
    return pd.Timedelta(tf)


def map_to_exec(s: pd.Series, setup_tf: str, exec_index: pd.DatetimeIndex) -> pd.Series:
    """Setup-TF series -> exec index; value becomes visible at setup-bar close."""
    shifted = s.set_axis(s.index + tf_delta(setup_tf))
    return shifted.reindex(exec_index, method="ffill")


def activation_positions(signal_ts: pd.DatetimeIndex, setup_tf: str,
                         exec_index: pd.DatetimeIndex) -> np.ndarray:
    """First exec-bar position at/after each setup signal's close time."""
    return exec_index.searchsorted(signal_ts + tf_delta(setup_tf))


@njit(cache=True)
def breakout_machine(n, act_idx, dirs, trig, stop, exp_idx, open_, high, low):
    """Armed-level breakout with expiry, one armed slot per direction.

    A signal arms a trigger level from its activation bar through exp_idx
    (inclusive); a newer signal in the same direction overwrites the older one.
    Long fills at max(open, trigger) on the first bar whose high breaks the
    trigger; short mirrors. Returns per-bar entry flags, fill price, and the
    structural stop as a fraction of fill price (NaN when no stop level given).
    """
    long_e = np.zeros(n, np.bool_)
    short_e = np.zeros(n, np.bool_)
    entry_price = np.full(n, np.nan)
    sl_pct = np.full(n, np.nan)
    lt = 0.0; ls = np.nan; le = -1
    st = 0.0; ss = np.nan; se = -1
    k = 0
    m = act_idx.shape[0]
    for i in range(n):
        while k < m and act_idx[k] <= i:
            if dirs[k] > 0:
                lt, ls, le = trig[k], stop[k], exp_idx[k]
            else:
                st, ss, se = trig[k], stop[k], exp_idx[k]
            k += 1
        if le >= i and high[i] > lt:
            long_e[i] = True
            p = open_[i] if open_[i] > lt else lt
            entry_price[i] = p
            if not np.isnan(ls):
                sl_pct[i] = (p - ls) / p
            le = -1
        if se >= i and low[i] < st:
            short_e[i] = True
            p = open_[i] if open_[i] < st else st
            if np.isnan(entry_price[i]):
                entry_price[i] = p
            if not np.isnan(ss):
                sl_pct[i] = (ss - p) / p
            se = -1
    return long_e, short_e, entry_price, sl_pct


def fffd_setup_signals(setup_df: pd.DataFrame, dev: float, strict: bool,
                       window: int = 20):
    """FFFD signals on the setup TF with their structural levels.

    Returns (signals DataFrame [ts, dir, trigger, stop], mid, upper, lower).
    Long: trigger = high of the inside candle, stop = min(low of outside
    candle, low of inside candle). Short mirrors.
    """
    close, high, low = setup_df["close"], setup_df["high"], setup_df["low"]
    long_sig, short_sig, lower, mid, upper = ind.fffd_signals(close, window, dev, strict)
    h, lo = high.to_numpy(), low.to_numpy()
    li = np.flatnonzero(long_sig.to_numpy())
    li = li[li >= 1]
    si = np.flatnonzero(short_sig.to_numpy())
    si = si[si >= 1]
    sig = pd.DataFrame(
        {
            "ts": close.index[li].append(close.index[si]),
            "dir": np.concatenate([np.ones(len(li), np.int8), -np.ones(len(si), np.int8)]),
            "trigger": np.concatenate([h[li], lo[si]]),
            "stop": np.concatenate(
                [np.minimum(lo[li - 1], lo[li]), np.maximum(h[si - 1], h[si])]
            ),
        }
    ).sort_values("ts")
    return sig, mid, upper, lower


def didi_setup_signals(setup_df: pd.DataFrame, tol_bars: int, adx_filter: bool,
                       adx_threshold: float = 32.0):
    """Agulhada signals on the setup TF; trigger = signal-candle extreme."""
    close, high, low = setup_df["close"], setup_df["high"], setup_df["low"]
    alta, baixa = ind.agulhada_signals(close, tol_bars=tol_bars)
    if adx_filter:
        long_ok, short_ok = ind.adx_confirmation(high, low, close, threshold=adx_threshold)
        alta_f, baixa_f = alta & long_ok, baixa & short_ok
    else:
        alta_f, baixa_f = alta, baixa
    rows = [(ts, 1, high.loc[ts], np.nan) for ts in close.index[alta_f]]
    rows += [(ts, -1, low.loc[ts], np.nan) for ts in close.index[baixa_f]]
    sig = pd.DataFrame(rows, columns=["ts", "dir", "trigger", "stop"]).sort_values("ts")
    return sig, alta, baixa  # unfiltered alta/baixa for opposite-signal exits


def _session_days(index: pd.DatetimeIndex, symbol: str | None) -> np.ndarray:
    from specula.data import is_equity

    tz = "America/New_York" if symbol and is_equity(symbol) else "UTC"
    return pd.factorize(index.tz_convert(tz).date)[0]


def filter_same_session(sig: pd.DataFrame, setup_df: pd.DataFrame,
                        setup_tf: str, symbol: str) -> pd.DataFrame:
    """Keep only signals whose fechou-fora and fechou-dentro candles belong
    to the same session day (intraday-only mode)."""
    if sig.empty:
        return sig
    days = _session_days(setup_df.index, symbol)
    pos = setup_df.index.get_indexer(pd.DatetimeIndex(sig["ts"]))
    ok = (pos >= 1) & (days[pos] == days[np.maximum(pos - 1, 0)])
    return sig[ok]


def run_breakout(sig: pd.DataFrame, setup_tf: str, exec_df: pd.DataFrame,
                 validity_bars: int = 2, same_session: bool = False,
                 symbol: str | None = None):
    """Map setup signals onto exec bars and run the breakout machine.
    same_session: the armed trigger must fire in the SAME session day the
    signal candle closed in — no overnight carry."""
    n = len(exec_df)
    if sig.empty:
        return (np.zeros(n, bool), np.zeros(n, bool),
                np.full(n, np.nan), np.full(n, np.nan))
    ts = pd.DatetimeIndex(sig["ts"])
    act = activation_positions(ts, setup_tf, exec_df.index)
    expiry_ts = ts + (validity_bars + 1) * tf_delta(setup_tf)
    exp = exec_df.index.searchsorted(expiry_ts) - 1
    keep = act < n
    if same_session:
        days = _session_days(exec_df.index, symbol)
        # session of the signal candle = session of the first exec bar
        # inside it; activation must land in that same session
        act_c = np.clip(act, 0, n - 1)
        sig_c = np.clip(exec_df.index.searchsorted(ts), 0, n - 1)
        keep &= days[act_c] == days[sig_c]
        # cap the trigger's life at the session's last exec bar
        last_of_day = pd.Series(np.arange(n)).groupby(days).transform("max")
        exp = np.minimum(exp, last_of_day.to_numpy()[act_c])
    return breakout_machine(
        n,
        act[keep].astype(np.int64),
        sig["dir"].to_numpy()[keep].astype(np.int8),
        sig["trigger"].to_numpy()[keep].astype(np.float64),
        sig["stop"].to_numpy()[keep].astype(np.float64),
        exp[keep].astype(np.int64),
        exec_df["open"].to_numpy(np.float64),
        exec_df["high"].to_numpy(np.float64),
        exec_df["low"].to_numpy(np.float64),
    )
