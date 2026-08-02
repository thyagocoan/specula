"""Out-of-sample validation of the higher-TF level filter.

Diagnosis found one clean negative pocket: longs entered 0-1% BELOW the 4h
EMA21 (overhead resistance) lose money (PF 0.77) while every other band is
profitable. This tests the derived filter — block longs in that band, plus a
softer variant also blocking over-extended longs (>3% above daily SMA50) —
against the unfiltered baseline on the five panel setups, walk-forward style,
with OOS trades pooled across setups.

Usage:
    uv run python scripts/level_validate_wf.py
"""

import sys

import numpy as np

from specula.sweeps import cfg_label
from specula.wf import aggregate_oos, candidate, evaluate_scenario, make_folds

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

VARIANTS = {
    "no-filter": None,
    "block-long-under-4hEMA21": {
        "ind": "level",
        "block_long": [{"col": "dist_ema21_4h", "lo": -1.0, "hi": 0.0}],
        "block_short": [],
    },
    "block+overextended": {
        "ind": "level",
        "block_long": [
            {"col": "dist_ema21_4h", "lo": -1.0, "hi": 0.0},
            {"col": "dist_sma50_1d", "lo": 3.0, "hi": 999.0},
        ],
        "block_short": [],
    },
}

MIN_TRAIN = 5


def main() -> int:
    pooled: dict[str, list] = {name: [] for name in VARIANTS}
    for base in SETUPS:
        for name, flt in VARIANTS.items():
            cfg = dict(base)
            if flt:
                cfg["filter"] = flt
            c = candidate(cfg)
            if not len(c["ts"]):
                continue
            folds = make_folds(c["ts"].min(), c["ts"].max())
            r = evaluate_scenario([c], folds, cfg_label, MIN_TRAIN)
            pooled[name].extend(r.pop("oos_raw"))
            a = r["aggregate"]
            print(f"{base['symbol']:>8} {name:>26}: OOS {a['oos_trades']:>3} "
                  f"trades, PF {a['oos_pf']}, ret {a['oos_return_pct']}%",
                  flush=True)

    print("\n=== pooled across the 5 setups (out-of-sample) ===")
    for name, oos in pooled.items():
        agg, _ = aggregate_oos(oos)
        print(f"{name:>26}: {agg['oos_trades']:>4} trades, "
              f"PF {agg['oos_pf']}, win {agg['oos_win_rate_pct']}%, "
              f"avg {agg['oos_avg_trade_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
