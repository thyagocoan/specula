"""Columnar MA-crossover megasweep — vectorbt's native broadcasting fast path.

Per symbol: every fast×slow window pair (fast < slow) × {sma, ema} × sl/tp
exit grid × per-class fees, all as columns in a handful of compiled passes on
15m bars. Reverse-cross acts as the opposite-direction entry (Both mode);
equities additionally get EOD-flat and the late-entry block.

Registry discipline: the full stat matrix goes to
data/meta/megasweep/{symbol}.parquet; only the top-10 plateau-passing rows
per symbol are logged to the registry (tag lab-ma-v1).

Usage:
    uv run python scripts/megasweep_ma.py [--symbols A,B,...] [--limit N]
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from specula import runlog
from specula.backtest import INIT_CASH, SLIPPAGE, eod_masks, frames
from specula.data import equity_symbols, is_equity
from specula.sweeps import EQUITY_FEES, FEES

EXEC_TF = "15min"
WINDOWS = [3, 5, 8, 10, 13, 16, 21, 26, 34, 42, 55, 75, 100, 130, 200]
EXITS = [(0.005, 0.005), (0.005, 0.01), (0.01, 0.005), (0.01, 0.01)]
OUT_DIR = Path("data/meta/megasweep")
SWEEP_TAG = "lab-ma-v1"
TOP_K = 10


def all_symbols() -> list[str]:
    crypto = sorted(p.name for p in Path("data/raw/binance/spot").glob("*"))
    return crypto + sorted(equity_symbols())


def sweep_symbol(symbol: str) -> pd.DataFrame:
    import vectorbt as vbt

    df = frames(symbol, EXEC_TF)
    close = df["close"]
    fees = EQUITY_FEES if is_equity(symbol) else FEES
    eod = late = None
    if is_equity(symbol):
        eod, late = eod_masks(df.index)
        eod = eod.to_numpy()[:, None]
        late = late.to_numpy()[:, None]

    rows = []
    for ma_type in ("sma", "ema"):
        fast_ma, slow_ma = vbt.MA.run_combs(
            close, window=WINDOWS, r=2, short_names=["fast", "slow"],
            ewm=(ma_type == "ema"),
        )
        cross_up = fast_ma.ma_crossed_above(slow_ma).to_numpy()
        cross_dn = fast_ma.ma_crossed_below(slow_ma).to_numpy()
        pairs = list(zip(
            fast_ma.wrapper.columns.get_level_values("fast_window"),
            slow_ma.wrapper.columns.get_level_values("slow_window"),
        ))
        n_pairs = cross_up.shape[1]
        n_ex = len(EXITS)
        entries = np.tile(cross_up, n_ex)
        short_entries = np.tile(cross_dn, n_ex)
        exits = np.tile(cross_dn, n_ex)
        short_exits = np.tile(cross_up, n_ex)
        if eod is not None:
            entries = entries & ~late & ~eod
            short_entries = short_entries & ~late & ~eod
            exits = exits | eod
            short_exits = short_exits | eod
        sl_row = np.repeat([e[0] for e in EXITS], n_pairs)[None, :]
        tp_row = np.repeat([e[1] for e in EXITS], n_pairs)[None, :]

        for fee in fees:
            pf = vbt.Portfolio.from_signals(
                close=close,
                entries=entries,
                exits=exits,
                short_entries=short_entries,
                short_exits=short_exits,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                sl_stop=sl_row,
                tp_stop=tp_row,
                fees=fee,
                slippage=SLIPPAGE,
                init_cash=INIT_CASH,
                freq=EXEC_TF,
            )
            n = pf.trades.count()
            pfac = pf.trades.profit_factor()
            win = pf.trades.win_rate()
            ret = pf.total_return()
            dd = pf.max_drawdown()
            for col in range(entries.shape[1]):
                f, s = pairs[col % n_pairs]
                sl, tp = EXITS[col // n_pairs]
                rows.append({
                    "symbol": symbol, "ma_type": ma_type,
                    "fast": int(f), "slow": int(s),
                    "sl": sl, "tp": tp, "fee": fee,
                    "n_trades": int(n.iloc[col]),
                    "profit_factor": float(pfac.iloc[col]) if pd.notna(pfac.iloc[col]) else None,
                    "win_rate_pct": round(100 * float(win.iloc[col]), 1) if pd.notna(win.iloc[col]) else None,
                    "total_return_pct": round(100 * float(ret.iloc[col]), 2),
                    "max_dd_pct": round(100 * float(dd.iloc[col]), 2),
                })
    out = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_DIR / f"{symbol}.parquet")
    return out


def plateau_top(df: pd.DataFrame, k: int = TOP_K) -> pd.DataFrame:
    """Top-k rows whose (fast, slow) grid neighbors are also profitable."""
    widx = {w: i for i, w in enumerate(WINDOWS)}
    pf_map = {}
    for r in df.itertuples():
        pf_map[(r.ma_type, r.sl, r.tp, r.fee, r.fast, r.slow)] = r.profit_factor

    def neighbor_ok(r) -> bool:
        fi, si = widx[r.fast], widx[r.slow]
        checked = good = 0
        for dfi, dsi in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nfi, nsi = fi + dfi, si + dsi
            if not (0 <= nfi < len(WINDOWS) and 0 <= nsi < len(WINDOWS)):
                continue
            nf, ns = WINDOWS[nfi], WINDOWS[nsi]
            if nf >= ns:
                continue
            v = pf_map.get((r.ma_type, r.sl, r.tp, r.fee, nf, ns))
            if v is None:
                continue
            checked += 1
            good += v is not None and v > 1.0
        return checked > 0 and good >= max(1, checked // 2)

    viable = df[(df["n_trades"] >= 30) & (df["profit_factor"].notna())].copy()
    viable = viable[viable.apply(neighbor_ok, axis=1)]
    return viable.sort_values("profit_factor", ascending=False).head(k)


def to_registry(symbol: str, top: pd.DataFrame, sha: str) -> list[dict]:
    rows = []
    for r in top.itertuples():
        cfg = dict(
            strategy="lab", symbol=symbol, setup_tf=EXEC_TF, exec_tf=EXEC_TF,
            entry=dict(kind="ma_cross", ma_type=r.ma_type, fast=r.fast, slow=r.slow),
            exit=dict(kind="fixed_r", sl=r.sl, tp=r.tp),
            fee=r.fee,
        )
        metrics = dict(
            n_trades=r.n_trades, total_return_pct=r.total_return_pct,
            max_dd_pct=r.max_dd_pct, win_rate_pct=r.win_rate_pct,
            profit_factor=round(r.profit_factor, 3), avg_trade_pct=None, sharpe=None,
        )
        rows.append(runlog.make_row(cfg, metrics, SWEEP_TAG, sha))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--limit", type=int, default=0, help="cap symbol count (smoke test)")
    args = ap.parse_args()

    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               or all_symbols())
    if args.limit:
        symbols = symbols[: args.limit]

    t0 = time.monotonic()
    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"MA megasweep: {len(symbols)} symbols, "
          f"{len(WINDOWS) * (len(WINDOWS) - 1) // 2 * 2 * len(EXITS)} combos/symbol/fee, "
          f"{workers} workers", flush=True)

    sha = runlog.git_sha()
    reg_rows = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for symbol, df in zip(symbols, ex.map(sweep_symbol, symbols)):
            done += 1
            top = plateau_top(df)
            reg_rows += to_registry(symbol, top, sha)
            best = top.iloc[0] if len(top) else None
            msg = (f"PF {best['profit_factor']:.2f} {best['ma_type']} "
                   f"{best['fast']}/{best['slow']}" if best is not None
                   else "no plateau survivor")
            print(f"[{done}/{len(symbols)}] {symbol}: {msg} "
                  f"({time.monotonic() - t0:.0f}s)", flush=True)

    if reg_rows:
        runlog.append(reg_rows)
    print(f"\nlogged {len(reg_rows)} plateau survivors to registry in "
          f"{(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
