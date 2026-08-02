"""Support/resistance diagnosis: do higher-timeframe levels separate winning
from losing trades in our validated setups?

For a panel of walk-forward-surviving setups, join every trade with the
distance to higher-TF levels at entry (daily SMA200/50, 4h EMA21, prior-day
high/low) and report profit factor per distance bucket, split by direction.
All in-sample diagnosis — any filter derived from it must pass walk-forward.

Usage:
    uv run python scripts/sr_diagnosis.py
"""

import sys

import numpy as np
import pandas as pd

from specula.backtest import build_portfolio
from specula.features import level_matrix

SETUPS = [
    dict(strategy="fffd", symbol="BTCUSDT", setup_tf="2h", exec_tf="1min",
         dev=2.0, strict=True, target="r1", fee=0.0004),
    dict(strategy="didi", symbol="LLY", setup_tf="15min", exec_tf="5min",
         tol_bars=1, adx_filter=False, sl=0.01, tp=0.005, fee=0.0001),
    dict(strategy="fffd", symbol="GOOGL", setup_tf="1h", exec_tf="1min",
         dev=2.0, strict=True, target="r1", fee=0.0001),
    dict(strategy="fffd", symbol="TSLA", setup_tf="1h", exec_tf="15min",
         dev=2.0, strict=False, target="midband", fee=0.0001),
    dict(strategy="fffd", symbol="AVGO", setup_tf="1h", exec_tf="30min",
         dev=2.5, strict=False, target="midband", fee=0.0001),
]

BANDS = [-np.inf, -3, -1, 0, 1, 3, np.inf]


def pf(r: np.ndarray) -> float:
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    return float("inf") if l == 0 and w > 0 else (0.0 if l == 0 else float(w / l))


def collect() -> pd.DataFrame:
    frames_out = []
    for cfg in SETUPS:
        p = build_portfolio(cfg)
        rec = p.trades.records
        rec = rec[rec["status"] == 1]
        idx = p.wrapper.index
        lm = level_matrix(cfg["symbol"], cfg["exec_tf"])
        entry_pos = rec["entry_idx"].to_numpy()
        df = pd.DataFrame({
            "symbol": cfg["symbol"],
            "dir": np.where(rec["direction"].to_numpy() == 0, "long", "short"),
            "ret": rec["return"].to_numpy(),
        })
        for col in lm.columns:
            df[col] = lm[col].to_numpy()[entry_pos]
        frames_out.append(df)
        print(f"[done] {cfg['symbol']}: {len(df)} trades", flush=True)
    return pd.concat(frames_out, ignore_index=True)


def report(t: pd.DataFrame) -> None:
    print(f"\npooled: {len(t)} trades from {t['symbol'].nunique()} setups, "
          f"overall PF {pf(t['ret'].to_numpy()):.2f}")
    for col in [c for c in t.columns if c.startswith("dist_")]:
        for d in ("long", "short"):
            g = t[t["dir"] == d]
            if len(g) < 20:
                continue
            cut = pd.cut(g[col], BANDS)
            rows = []
            for b, grp in g.groupby(cut, observed=True):
                if len(grp) < 5:
                    continue
                rows.append({"band_%": str(b), "n": len(grp),
                             "pf": round(pf(grp["ret"].to_numpy()), 2),
                             "avg_%": round(100 * grp["ret"].mean(), 3)})
            if rows:
                print(f"\n--- {col} | {d}s ({len(g)}) ---")
                print(pd.DataFrame(rows).to_string(index=False))

    # focused hypothesis checks
    print("\n=== hypothesis checks ===")
    checks = [
        ("longs near/above RISING daily SMA50 (0..2%, slope+)",
         (t["dir"] == "long") & t["dist_sma50_1d"].between(0, 2)
         & (t["slope_sma50_1d"] > 0)),
        ("longs far below daily SMA50 (< -3%)",
         (t["dir"] == "long") & (t["dist_sma50_1d"] < -3)),
        ("shorts just under prior-day high (-0.5..0%)",
         (t["dir"] == "short") & t["dist_pdh"].between(-0.5, 0)),
        ("longs just above prior-day low (0..0.5%)",
         (t["dir"] == "long") & t["dist_pdl"].between(0, 0.5)),
        ("shorts above RISING daily SMA50 (fighting trend)",
         (t["dir"] == "short") & (t["dist_sma50_1d"] > 0)
         & (t["slope_sma50_1d"] > 0)),
    ]
    for name, mask in checks:
        g = t[mask]
        if len(g) >= 8:
            print(f"{name}: n={len(g)}, PF {pf(g['ret'].to_numpy()):.2f}, "
                  f"avg {100 * g['ret'].mean():+.3f}%")
        else:
            print(f"{name}: n={len(g)} (too few)")


def main() -> int:
    t = collect()
    report(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
