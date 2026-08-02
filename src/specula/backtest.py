"""Build a Portfolio from a config dict — the single entry point used by the
sweep, the run registry, and on-demand plotting, so any logged run can be
reproduced exactly from its stored params."""

import numpy as np
import pandas as pd
import vectorbt as vbt

from specula import mtf
from specula.data import (is_equity, load_crypto_1m, load_equity_1m,
                          resample_equity, resample_ohlcv)

from specula.settings import get_settings

SLIPPAGE = 0.0001
_SETTINGS = get_settings()
INIT_CASH = _SETTINGS["capital_usd"]
EOD_ENTRY_CUTOFF_MIN = 15 * 60 + 45  # no new entries from 15:45 ET


def trade_size_for(symbol: str) -> float:
    """Configured USD size per trade for the symbol's asset class (0 = all-in)."""
    key = ("trade_size_crypto_usd" if symbol.endswith(("USDT", "USDC"))
           else "trade_size_stock_usd")
    return float(_SETTINGS.get(key, 0.0))

_resample_cache: dict[tuple[str, str], pd.DataFrame] = {}
_signal_cache: dict[tuple, object] = {}
_breakout_cache: dict[tuple, tuple] = {}


def frames(symbol: str, tf: str) -> pd.DataFrame:
    key = (symbol, tf)
    if key not in _resample_cache:
        if is_equity(symbol):
            _resample_cache[key] = resample_equity(
                load_equity_1m(symbol, session="regular"), tf
            )
        else:
            _resample_cache[key] = resample_ohlcv(load_crypto_1m(symbol), tf)
    return _resample_cache[key]


def _cached(cache: dict, key: tuple, fn):
    if key not in cache:
        cache[key] = fn()
    return cache[key]


def eod_masks(exec_index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """(last-bar-of-session exit mask, late-entry block mask) for equities."""
    ny = exec_index.tz_convert("America/New_York")
    day = pd.Series(ny.date, index=exec_index)
    eod = day != day.shift(-1)
    late = pd.Series(ny.hour * 60 + ny.minute >= EOD_ENTRY_CUTOFF_MIN,
                     index=exec_index)
    return eod, late


def build_portfolio(cfg: dict) -> vbt.Portfolio:
    setup_df = frames(cfg["symbol"], cfg["setup_tf"])
    exec_df = frames(cfg["symbol"], cfg["exec_tf"])
    n = len(exec_df)
    price = exec_df["close"].copy()
    sl_trail = False

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
        elif target == "none":
            pass  # structural stop only — used for MFE measurement
        elif target == "trail":
            sl_trail = True
            dist = cfg["trail"]
            if dist != "structural":
                sl_arr = pd.Series(
                    np.where(long_e | short_e, float(dist), np.nan),
                    index=exec_df.index,
                )
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

    elif cfg["strategy"] == "lab":
        import json as _json

        from specula import signals as sig

        sig_key = ("lab", cfg["symbol"], cfg["setup_tf"], cfg["exec_tf"],
                   _json.dumps(cfg["entry"], sort_keys=True))
        res = _cached(
            _signal_cache, sig_key,
            lambda: sig.generate(cfg["entry"], cfg["symbol"],
                                 cfg["setup_tf"], cfg["exec_tf"]),
        )
        sl_hint = None
        if len(res) == 4:
            long_e, short_e, price_hint, sl_hint = res
        else:
            long_e, short_e, price_hint = res
        entries = long_e.copy()
        short_entries = short_e.copy()
        if price_hint is not None:
            price[entries] = price_hint[entries]
            price[short_entries] = price_hint[short_entries]
        exits = pd.Series(False, index=exec_df.index)
        short_exits = pd.Series(False, index=exec_df.index)
        exit_spec = cfg["exit"]
        sl_stop = exit_spec.get("sl", np.nan)
        tp_stop = exit_spec.get("tp", np.nan)
        if exit_spec["kind"] == "trail":
            sl_trail = True
            if sl_stop == "structural":
                # per-trade trailing distance from the signal (entry→level)
                sl_stop = sl_hint if sl_hint is not None else np.nan
        elif exit_spec["kind"] == "time":
            # exit N exec bars after the entry signal (approximation: keyed to
            # the signal bar, exact when entries don't overlap within N bars)
            n_bars = int(exit_spec["max_bars"])
            exits = entries.shift(n_bars, fill_value=False)
            short_exits = short_entries.shift(n_bars, fill_value=False)

    else:
        raise ValueError(f"unknown strategy {cfg['strategy']}")

    flt = cfg.get("filter")
    if flt:
        from specula.features import (level_entry_mask, regime_entry_mask,
                                      rsi_entry_mask)

        if flt.get("ind") == "rsi":
            long_ok, short_ok = rsi_entry_mask(cfg["symbol"], cfg["exec_tf"], flt)
        elif flt.get("ind") == "level":
            long_ok, short_ok = level_entry_mask(cfg["symbol"], cfg["exec_tf"], flt)
        elif flt.get("ind") in ("gap", "compression", "trend", "session",
                                "vix", "event"):
            long_ok, short_ok = regime_entry_mask(cfg["symbol"], cfg["exec_tf"], flt)
        else:
            raise ValueError(f"unknown filter indicator {flt.get('ind')}")
        entries = entries & long_ok
        short_entries = short_entries & short_ok

    if is_equity(cfg["symbol"]):
        # intraday only: flat by the close, no fresh entries near it
        eod, late = eod_masks(exec_df.index)
        entries = entries & ~late & ~eod
        short_entries = short_entries & ~late & ~eod
        exits = exits | eod
        short_exits = short_exits | eod

    size_kwargs = {}
    trade_size = trade_size_for(cfg["symbol"])
    if trade_size > 0:
        size_kwargs = {"size": trade_size, "size_type": "value"}
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
        sl_trail=sl_trail,
        tp_stop=tp_stop,
        fees=cfg["fee"],
        slippage=SLIPPAGE,
        init_cash=INIT_CASH,
        freq=cfg["exec_tf"],
        **size_kwargs,
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
