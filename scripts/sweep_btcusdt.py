"""First parameter sweep: Didi agulhada + Bollinger FFFD on BTCUSDT, 5m/15m.

Runs every combination as its own single-column Portfolio.from_signals call
(simple and memory-safe at this scale), collects compact per-run stats, and
writes data/meta/sweep_btcusdt.parquet.

Cost scenarios per side: 0.04% (futures taker) and 0.10% (spot taker), plus
0.01% slippage on every fill. Stops/targets are evaluated intra-bar against
real high/low.

Usage:
    uv run python scripts/sweep_btcusdt.py
"""

import itertools
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from specula import indicators as ind
from specula.data import load_crypto_1m, resample_ohlcv

FEES = [0.0004, 0.001]
SLIPPAGE = 0.0001
TIMEFRAMES = ["5min", "15min"]
INIT_CASH = 100_000


def run_portfolio(df, entries, exits, short_entries, short_exits, price,
                  sl, tp, fee, freq):
    return vbt.Portfolio.from_signals(
        close=df["close"],
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        price=price,
        open=df["open"],
        high=df["high"],
        low=df["low"],
        sl_stop=sl,
        tp_stop=tp,
        fees=fee,
        slippage=SLIPPAGE,
        init_cash=INIT_CASH,
        freq=freq,
    )


def collect(pf, meta: dict) -> dict:
    trades = pf.trades
    n = trades.count()
    row = dict(meta)
    row.update(
        n_trades=int(n),
        total_return_pct=round(100 * float(pf.total_return()), 2),
        max_dd_pct=round(100 * float(pf.max_drawdown()), 2),
    )
    if n > 0:
        returns = trades.returns.values
        row.update(
            win_rate_pct=round(100 * float(trades.win_rate()), 1),
            profit_factor=round(float(trades.profit_factor()), 3),
            avg_trade_pct=round(100 * float(np.mean(returns)), 3),
            median_trade_pct=round(100 * float(np.median(returns)), 3),
            sharpe=round(float(pf.sharpe_ratio()), 2),
        )
    return row


def sweep_didi(df: pd.DataFrame, freq: str) -> list[dict]:
    rows = []
    close = df["close"]
    adx_long, adx_short = ind.adx_confirmation(df["high"], df["low"], close)
    for tol, delay, use_adx in itertools.product([0, 1], [1, 2], [False, True]):
        alta, baixa = ind.agulhada_signals(close, tol_bars=tol)
        long_e, short_e = alta, baixa
        if use_adx:
            long_e = alta & adx_long
            short_e = baixa & adx_short
        # signal on close of bar t -> execute at open of bar t+delay
        entries = long_e.shift(delay, fill_value=False)
        short_entries = short_e.shift(delay, fill_value=False)
        exits = baixa.shift(delay, fill_value=False)      # unfiltered opposite signal
        short_exits = alta.shift(delay, fill_value=False)
        for sl, tp, fee in itertools.product([0.005, 0.01], [0.005, 0.01], FEES):
            pf = run_portfolio(df, entries, exits, short_entries, short_exits,
                               df["open"], sl, tp, fee, freq)
            rows.append(collect(pf, dict(
                strategy="didi", timeframe=freq, tol_bars=tol, entry_delay=delay,
                adx_filter=use_adx, variant="", sl=sl, tp=tp, fee=fee,
            )))
    return rows


def sweep_fffd(df: pd.DataFrame, freq: str) -> list[dict]:
    rows = []
    close = df["close"]
    for dev, strict, entry_mode in itertools.product(
        [2.0, 2.5], [True, False], ["close", "next_open"]
    ):
        long_sig, short_sig, lower, mid, upper = ind.fffd_signals(
            close, window=20, dev=dev, strict=strict
        )
        mid_long_x, mid_short_x = ind.midband_cross_exits(close, mid)
        shift = 0 if entry_mode == "close" else 1
        price = close if entry_mode == "close" else df["open"]
        entries = long_sig.shift(shift, fill_value=False)
        short_entries = short_sig.shift(shift, fill_value=False)
        for sl, target, fee in itertools.product(
            [0.005, 0.01], ["tp_half", "tp_one", "midband"], FEES
        ):
            if target == "midband":
                exits = mid_long_x.shift(shift, fill_value=False)
                short_exits = mid_short_x.shift(shift, fill_value=False)
                tp = np.nan
            else:
                exits = short_exits = pd.Series(False, index=close.index)
                tp = 0.005 if target == "tp_half" else 0.01
            pf = run_portfolio(df, entries, exits, short_entries, short_exits,
                               price, sl, tp, fee, freq)
            rows.append(collect(pf, dict(
                strategy="fffd", timeframe=freq, tol_bars=-1, entry_delay=shift,
                adx_filter=False, variant=f"dev{dev}|{'strict' if strict else 'loose'}|{target}",
                sl=sl, tp=tp, fee=fee,
            )))
    return rows


def main() -> int:
    t0 = time.monotonic()
    df_1m = load_crypto_1m("BTCUSDT")
    rows = []
    for freq in TIMEFRAMES:
        df = resample_ohlcv(df_1m, freq)
        bh = 100 * (df["close"].iloc[-1] / df["close"].iloc[0] - 1)
        print(f"[{freq}] {len(df)} bars, buy&hold {bh:.1f}%", flush=True)
        rows += sweep_didi(df, freq)
        print(f"[{freq}] didi done ({time.monotonic() - t0:.0f}s)", flush=True)
        rows += sweep_fffd(df, freq)
        print(f"[{freq}] fffd done ({time.monotonic() - t0:.0f}s)", flush=True)

    out = pd.DataFrame(rows)
    dest = Path("data/meta/sweep_btcusdt.parquet")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dest)
    print(f"\n{len(out)} runs in {(time.monotonic() - t0) / 60:.1f} min -> {dest}", flush=True)

    viable = out[out["n_trades"] >= 30].copy()
    for metric in ["profit_factor", "total_return_pct"]:
        print(f"\n=== top 10 by {metric} (n_trades >= 30) ===")
        cols = ["strategy", "timeframe", "variant", "tol_bars", "entry_delay", "adx_filter",
                "sl", "tp", "fee", "n_trades", "win_rate_pct", "profit_factor",
                "avg_trade_pct", "total_return_pct", "max_dd_pct", "sharpe"]
        print(viable.sort_values(metric, ascending=False).head(10)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
