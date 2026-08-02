"""Trailing-stop experiment on the walk-forward-surviving FFFD setup:
FFFD 2h -> 1min, strict, dev 2.0 (baseline target: r1).

Part A — MFE: simulate the setup with the structural stop only (no target)
and measure each trade's maximum favorable excursion, i.e. how far price ran
in our favor before the trade ended. This is the ceiling any exit rule can
capture, and tells us what a "decent" trail distance is.

Part B — trail sweep: pure trailing exits at several distances (plus the
structural distance itself), vs. the r1/r2 fixed-target baselines, at both
fee scenarios. Every run is logged to the registry (sweep_tag trail-fffd-v1)
so it shows up in the web portal.

Usage:
    uv run python scripts/trailing_fffd.py
"""

import sys
import time

import numpy as np
import pandas as pd
from numba import njit

from specula import runlog
from specula.backtest import build_portfolio, collect_metrics, frames
from specula.sweeps import FEES

BASE = dict(strategy="fffd", symbol="BTCUSDT", setup_tf="2h", exec_tf="1min",
            dev=2.0, strict=True)
TRAILS = [0.0025, 0.005, 0.0075, 0.01, 0.015, "structural"]
SWEEP_TAG = "trail-fffd-v1"


@njit(cache=True)
def _mfe(entry_idx, exit_idx, direction, entry_price, high, low):
    out = np.empty(entry_idx.shape[0])
    for i in range(entry_idx.shape[0]):
        a, b = entry_idx[i], exit_idx[i]
        if direction[i] == 0:  # long
            m = high[a]
            for j in range(a, b + 1):
                if high[j] > m:
                    m = high[j]
            out[i] = m / entry_price[i] - 1.0
        else:  # short
            m = low[a]
            for j in range(a, b + 1):
                if low[j] < m:
                    m = low[j]
            out[i] = 1.0 - m / entry_price[i]
    return out


def mfe_analysis(fee: float) -> None:
    cfg = dict(BASE, target="none", fee=fee)
    pf = build_portfolio(cfg)
    rec = pf.trades.records
    rec = rec[rec["status"] == 1]
    exec_df = frames(BASE["symbol"], BASE["exec_tf"])
    mfe = _mfe(
        rec["entry_idx"].to_numpy(np.int64),
        rec["exit_idx"].to_numpy(np.int64),
        rec["direction"].to_numpy(np.int64),
        rec["entry_price"].to_numpy(np.float64),
        exec_df["high"].to_numpy(np.float64),
        exec_df["low"].to_numpy(np.float64),
    )
    q = np.quantile(mfe, [0.25, 0.5, 0.75, 0.9])
    print(f"\n=== MFE (stop-only exits, fee {fee * 100:.2f}%) — {len(mfe)} trades ===")
    print(f"how far trades ran in our favor before ending:")
    print(f"  25th pct: {q[0] * 100:5.2f}%   median: {q[1] * 100:5.2f}%   "
          f"75th pct: {q[2] * 100:5.2f}%   90th pct: {q[3] * 100:5.2f}%")
    print(f"  mean: {mfe.mean() * 100:.2f}%   share reaching >=0.5%: "
          f"{(mfe >= 0.005).mean() * 100:.0f}%   >=1%: {(mfe >= 0.01).mean() * 100:.0f}%")


def main() -> int:
    t0 = time.monotonic()
    for fee in FEES:
        mfe_analysis(fee)

    rows = []
    results = []
    sha = runlog.git_sha()
    configs = (
        [dict(BASE, target="trail", trail=t, fee=fee) for t in TRAILS for fee in FEES]
        + [dict(BASE, target=t, fee=fee) for t in ("r1", "r2") for fee in FEES]
    )
    for cfg in configs:
        m = collect_metrics(build_portfolio(cfg))
        rows.append(runlog.make_row(cfg, m, SWEEP_TAG, sha))
        label = (f"trail {cfg['trail']}" if cfg["target"] == "trail"
                 else f"target {cfg['target']}")
        results.append({"variant": label, "fee": cfg["fee"], **m})
        print(f"[done] {label:>18} fee {cfg['fee'] * 100:.2f}%: "
              f"PF {m.get('profit_factor')}, {m['n_trades']} trades, "
              f"return {m['total_return_pct']}%", flush=True)

    runlog.append(rows)
    out = pd.DataFrame(results).sort_values(["fee", "profit_factor"], ascending=[True, False])
    print(f"\n=== comparison ({(time.monotonic() - t0) / 60:.1f} min) ===")
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
