"""Export daily equity curves for each symbol's best setup to the web app.

For every symbol in the registry, picks the best config (profit factor at the
lowest fee, min 30 trades — relaxed to 10 if none qualify), simulates it once,
and writes a daily-resampled equity multiple to web/public/data/curves.json.
These curves power the Overview's period P&L chart (labeled in-sample; the
walk-forward OOS curves come from walkforward.json).

Usage:
    uv run python scripts/export_curves.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from specula import runlog
from specula.backtest import INIT_CASH, build_portfolio
from specula.sweeps import cfg_label

OUT = Path("web/public/data/curves.json")


def best_cfg_per_symbol(df: pd.DataFrame) -> dict[str, dict]:
    df = df[df["profit_factor"].notna()].copy()
    out = {}
    for symbol, g in df.groupby("symbol"):
        fee_min = min(json.loads(p)["fee"] for p in g["params"])
        g = g[[json.loads(p)["fee"] == fee_min for p in g["params"]]]
        solid = g[g["n_trades"] >= 30]
        pick = (solid if len(solid) else g[g["n_trades"] >= 10])
        if not len(pick):
            continue
        row = pick.sort_values("profit_factor", ascending=False).iloc[0]
        cfg = json.loads(row["params"])
        cfg.pop("_variant", None)
        out[symbol] = cfg
    return out


def main() -> int:
    t0 = time.monotonic()
    cfgs = best_cfg_per_symbol(runlog.load())
    curves = {}
    for symbol, cfg in sorted(cfgs.items()):
        try:
            pf = build_portfolio(cfg)
            daily = (pf.value().resample("1d").last().dropna() / INIT_CASH)
            curves[symbol] = {
                "label": cfg_label(cfg, with_fee=True),
                "points": [
                    {"t": str(ts.date()), "v": round(float(v), 4)}
                    for ts, v in daily.items()
                ],
            }
            print(f"[done] {symbol}: {len(daily)} daily points ({curves[symbol]['label']})",
                  flush=True)
        except Exception as e:
            print(f"[error] {symbol}: {type(e).__name__}: {e}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "in-sample best setup per symbol, daily equity multiple",
        "curves": curves,
    }), encoding="utf-8")
    print(f"\nwrote {OUT} ({len(curves)} symbols) in {(time.monotonic() - t0) / 60:.1f} min",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
