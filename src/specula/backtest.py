"""Build a Portfolio from a config dict — the single entry point used by the
sweep, the run registry, and on-demand plotting, so any logged run can be
reproduced exactly from its stored params."""

import numpy as np
import pandas as pd
import vectorbt as vbt

from specula import mtf
from specula.data import load_crypto_1m, resample_ohlcv

SLIPPAGE = 0.0001
INIT_CASH = 100_000

_resample_cache: dict[tuple[str, str], pd.DataFrame] = {}
_signal_cache: dict[tuple, object] = {}
_breakout_cache: dict[tuple, tuple] = {}


def frames(symbol: str, tf: str) -> pd.DataFrame:
    key = (symbol, tf)
    if key not in _resample_cache:
        _resample_cache[key] = resample_ohlcv(load_crypto_1m(symbol), tf)
    return _resample_cache[key]


def _cached(cache: dict, key: tuple, fn):
    if key not in cache:
        cache[key] = fn()
    return cache[key]


def build_portfolio(cfg: dict) -> vbt.Portfolio:
    setup_df = frames(cfg["symbol"], cfg["setup_tf"])
    exec_df = frames(cfg["symbol"], cfg["exec_tf"])
    n = len(exec_df)
    price = exec_df["close"].copy()

    if cfg["strategy"] == "fffd":
        sig_key = ("fffd", cfg["symbol"], cfg["setup_tf"], cfg["dev"], cfg["strict"])
        sig, mid, upper, lower = _cached(
            _signal_cache, sig_key,
            lambda: mtf.fffd_setup_signals(setup_df, dev=cfg["dev"], strict=cfg["strict"]),
        )
        long_e, short_e, entry_price, sl_pct = _cached(
            _breakout_cache, sig_key + (cfg["exec_tf"],),
            lambda: mtf.run_breakout(sig, cfg["setup_tf"], exec_df),
        )
        sl_arr = pd.Series(sl_pct, index=exec_df.index)
        tp_arr = pd.Series(np.nan, index=exec_df.index)
        exits = short_exits = pd.Series(False, index=exec_df.index)
        target = cfg["target"]
        if target in ("r1", "r2"):
            k = 1.0 if target == "r1" else 2.0
            tp_arr = sl_arr * k
        else:
            long_band = mtf.map_to_exec(mid if target == "midband" else upper,
                                        cfg["setup_tf"], exec_df.index)
            short_band = mtf.map_to_exec(mid if target == "midband" else lower,
                                         cfg["setup_tf"], exec_df.index)
            exits = exec_df["high"] >= long_band
            short_exits = exec_df["low"] <= short_band
            price[exits] = long_band[exits].clip(exec_df["low"], exec_df["high"])
            price[short_exits] = short_band[short_exits].clip(
                exec_df["low"], exec_df["high"]
            )
        entries = pd.Series(long_e, index=exec_df.index)
        short_entries = pd.Series(short_e, index=exec_df.index)
        price[entries] = entry_price[long_e]
        price[short_entries] = entry_price[short_e]
        sl_stop, tp_stop = sl_arr, tp_arr

    elif cfg["strategy"] == "didi":
        sig_key = ("didi", cfg["symbol"], cfg["setup_tf"], cfg["tol_bars"], cfg["adx_filter"])
        sig, alta_raw, baixa_raw = _cached(
            _signal_cache, sig_key,
            lambda: mtf.didi_setup_signals(
                setup_df, tol_bars=cfg["tol_bars"], adx_filter=cfg["adx_filter"]
            ),
        )
        long_e, short_e, entry_price, _ = _cached(
            _breakout_cache, sig_key + (cfg["exec_tf"],),
            lambda: mtf.run_breakout(sig, cfg["setup_tf"], exec_df),
        )
        entries = pd.Series(long_e, index=exec_df.index)
        short_entries = pd.Series(short_e, index=exec_df.index)
        price[entries] = entry_price[long_e]
        price[short_entries] = entry_price[short_e]
        # exit on the opposite (unfiltered) agulhada, at its exec activation bar
        exits = pd.Series(False, index=exec_df.index)
        short_exits = pd.Series(False, index=exec_df.index)
        for raw, series in ((baixa_raw, exits), (alta_raw, short_exits)):
            ts = setup_df.index[raw]
            pos = mtf.activation_positions(pd.DatetimeIndex(ts), cfg["setup_tf"],
                                           exec_df.index)
            pos = pos[pos < n]
            series.iloc[pos] = True
            price.iloc[pos] = exec_df["open"].iloc[pos]
        sl_stop, tp_stop = cfg["sl"], cfg["tp"]

    else:
        raise ValueError(f"unknown strategy {cfg['strategy']}")

    return vbt.Portfolio.from_signals(
        close=exec_df["close"],
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        price=price,
        open=exec_df["open"],
        high=exec_df["high"],
        low=exec_df["low"],
        sl_stop=sl_stop,
        tp_stop=tp_stop,
        fees=cfg["fee"],
        slippage=SLIPPAGE,
        init_cash=INIT_CASH,
        freq=cfg["exec_tf"],
    )


def collect_metrics(pf: vbt.Portfolio) -> dict:
    trades = pf.trades
    n = int(trades.count())
    row = dict(
        n_trades=n,
        total_return_pct=round(100 * float(pf.total_return()), 2),
        max_dd_pct=round(100 * float(pf.max_drawdown()), 2),
        win_rate_pct=np.nan, profit_factor=np.nan,
        avg_trade_pct=np.nan, sharpe=np.nan,
    )
    if n > 0:
        row.update(
            win_rate_pct=round(100 * float(trades.win_rate()), 1),
            profit_factor=round(float(trades.profit_factor()), 3),
            avg_trade_pct=round(100 * float(np.mean(trades.returns.values)), 3),
            sharpe=round(float(pf.sharpe_ratio()), 2),
        )
    return row
