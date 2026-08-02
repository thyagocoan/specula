"""Out-of-sample validation of the inverted RSI filter on FFFD 2h->1min.

The in-sample diagnosis showed a U-shape: FFFD trades entered while the
higher-TF RSI was at an extreme performed best; mid-range worst. This tests
the resulting filter honestly, three ways per fee scenario:

  1. baseline      — no filter, fixed config, fold-stitched OOS
  2. forced filter — each filter variant held fixed through all folds
  3. adaptive      — per fold, train-window PF picks among {none + variants}

min_train_trades is lowered to 8 because filtered variants trade less; noted
in the output. Results go to data/meta/rsi_validation.json and stdout.

Usage:
    uv run python scripts/rsi_validate_wf.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from specula.sweeps import FEES
from specula.wf import aggregate_oos, candidate, evaluate_scenario, make_folds

BASE = dict(strategy="fffd", symbol="BTCUSDT", setup_tf="2h", exec_tf="1min",
            dev=2.0, strict=True, target="r1")
MIN_TRAIN = 8

VARIANTS = {
    "no-filter": None,
    "rsi1d-out-40-60": {"ind": "rsi", "tf": "1d", "mode": "outside",
                        "long": [40, 60], "short": [40, 60]},
    "rsi1d-out-35-65": {"ind": "rsi", "tf": "1d", "mode": "outside",
                        "long": [35, 65], "short": [35, 65]},
    "rsi4h-out-40-60": {"ind": "rsi", "tf": "4h", "mode": "outside",
                        "long": [40, 60], "short": [40, 60]},
    "rsi4h-out-35-65": {"ind": "rsi", "tf": "4h", "mode": "outside",
                        "long": [35, 65], "short": [35, 65]},
    "rsi2h-out-40-60": {"ind": "rsi", "tf": "2h", "mode": "outside",
                        "long": [40, 60], "short": [40, 60]},
}


def variant_name(cfg: dict) -> str:
    return cfg.get("_variant", "?")


def main() -> int:
    t0 = time.monotonic()
    results = {}
    for fee in FEES:
        cands = []
        for name, flt in VARIANTS.items():
            cfg = dict(BASE, fee=fee, _variant=name)
            if flt:
                cfg["filter"] = flt
            c = candidate({k: v for k, v in cfg.items() if k != "_variant"})
            c["cfg"]["_variant"] = name
            cands.append(c)
            print(f"[table] {name} fee {fee}: {len(c['ts'])} trades", flush=True)

        all_ts = np.concatenate([c["ts"] for c in cands if len(c["ts"])])
        folds = make_folds(all_ts.min(), all_ts.max())

        rows = {}
        for c in cands:  # each variant held fixed through all folds
            r = evaluate_scenario([c], folds, variant_name, MIN_TRAIN)
            rows[c["cfg"]["_variant"]] = r["aggregate"]
        adaptive = evaluate_scenario(cands, folds, variant_name, MIN_TRAIN)
        rows["adaptive-selection"] = adaptive["aggregate"]
        results[str(fee)] = {
            "fixed": rows,
            "adaptive_folds": adaptive["folds"],
        }

        print(f"\n=== fee {fee * 100:.2f}%/side (min_train_trades={MIN_TRAIN}) ===",
              flush=True)
        for name, a in rows.items():
            print(f"  {name:>20}: OOS {a['oos_trades']:>3} trades, "
                  f"PF {a['oos_pf']}, win {a['oos_win_rate_pct']}%, "
                  f"return {a['oos_return_pct']}% "
                  f"({a['folds_with_winner']}/{a['folds_total']} folds)", flush=True)

    picks = [f["winner"] for f in results[str(FEES[0])]["adaptive_folds"] if f.get("winner")]
    print(f"\nadaptive picks at fee {FEES[0]}: {picks}", flush=True)

    out = Path("data/meta/rsi_validation.json")
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": BASE, "min_train_trades": MIN_TRAIN,
        "results": results,
    }, default=str), encoding="utf-8")
    print(f"wrote {out} in {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
