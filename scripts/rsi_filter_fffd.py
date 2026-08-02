"""RSI trade-quality analysis for FFFD 2h->1min (strict, dev 2.0, target r1).

Part A — diagnosis: run the unfiltered strategy, snapshot multi-timeframe RSI
at each trade's entry, and report performance per RSI bucket per timeframe.
Only monotone, economically sensible patterns deserve trust.

Part B — filter check: apply the hypothesis filter (skip longs when the
higher-TF RSI is deeply oversold — fading a still-falling knife — and skip
shorts when deeply overbought) and compare against baseline. Results are
logged to the registry (sweep_tag rsi-filter-v1) but are IN-SAMPLE: anything
promising must pass walk-forward before being believed.

Usage:
    uv run python scripts/rsi_filter_fffd.py
"""

import sys

import numpy as np
import pandas as pd

from specula import runlog
from specula.backtest import build_portfolio, collect_metrics, frames
from specula.features import FEATURE_TFS, rsi_matrix

BASE = dict(strategy="fffd", symbol="BTCUSDT", setup_tf="2h", exec_tf="1min",
            dev=2.0, strict=True, target="r1")
FEE = 0.0004
BUCKETS = [0, 30, 40, 50, 60, 70, 100]
SWEEP_TAG = "rsi-filter-v1"


def trades_with_features() -> pd.DataFrame:
    pf = build_portfolio(dict(BASE, fee=FEE))
    rec = pf.trades.records
    rec = rec[rec["status"] == 1]
    idx = pf.wrapper.index
    feats = rsi_matrix(BASE["symbol"], BASE["exec_tf"])
    entry_pos = rec["entry_idx"].to_numpy()
    out = pd.DataFrame({
        "entry_ts": idx[entry_pos],
        "direction": np.where(rec["direction"].to_numpy() == 0, "long", "short"),
        "ret": rec["return"].to_numpy(),
    })
    for col in feats.columns:
        out[col] = feats[col].to_numpy()[entry_pos]
    return out


def profit_factor(r: np.ndarray) -> float:
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    return float("inf") if l == 0 and w > 0 else (0.0 if l == 0 else float(w / l))


def bucket_report(t: pd.DataFrame) -> None:
    for tf in FEATURE_TFS:
        col = f"rsi_{tf}"
        cut = pd.cut(t[col], BUCKETS)
        rows = []
        for b, g in t.groupby(cut, observed=True):
            if len(g) == 0:
                continue
            rows.append({
                "rsi_bucket": str(b), "n": len(g),
                "win_rate": round(100 * (g["ret"] > 0).mean(), 1),
                "pf": round(profit_factor(g["ret"].to_numpy()), 2),
                "avg_ret_pct": round(100 * g["ret"].mean(), 3),
            })
        print(f"\n--- RSI({tf}) at entry, all {len(t)} trades ---")
        print(pd.DataFrame(rows).to_string(index=False))


def main() -> int:
    t = trades_with_features()
    print(f"baseline: {len(t)} trades, PF {profit_factor(t['ret'].to_numpy()):.3f}, "
          f"win {(100 * (t['ret'] > 0).mean()):.1f}%")
    bucket_report(t)

    # split by direction for the hypothesis check on higher TFs
    for tf in ["1d", "4h", "2h"]:
        col = f"rsi_{tf}"
        for d in ["long", "short"]:
            g = t[t["direction"] == d]
            lo = g[g[col] < 35] if d == "long" else g[g[col] > 65]
            if len(lo):
                print(f"\n{d}s with {col} {'<35' if d == 'long' else '>65'}: "
                      f"{len(lo)} trades, PF {profit_factor(lo['ret'].to_numpy()):.2f}, "
                      f"avg {100 * lo['ret'].mean():.3f}%")

    # Part B: hypothesis filters vs baseline (both fees), logged to registry
    print("\n=== filtered vs baseline (in-sample!) ===")
    rows, results = [], []
    sha = runlog.git_sha()
    variants = [("baseline", None)] + [
        (f"skip-extremes rsi_{tf} 35/65",
         {"ind": "rsi", "tf": tf, "window": 14, "long": [35, 100], "short": [0, 65]})
        for tf in ["1d", "4h", "2h"]
    ]
    for fee in [0.0004, 0.001]:
        for name, flt in variants:
            cfg = dict(BASE, fee=fee)
            if flt:
                cfg["filter"] = flt
            m = collect_metrics(build_portfolio(cfg))
            if flt:
                rows.append(runlog.make_row(cfg, m, SWEEP_TAG, sha))
            results.append({"variant": name, "fee": fee, **m})
    if rows:
        runlog.append(rows)
    out = pd.DataFrame(results)
    print(out[["variant", "fee", "n_trades", "win_rate_pct", "profit_factor",
               "total_return_pct", "max_dd_pct", "sharpe"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
