"""Multi-timeframe sweep on BTCUSDT: setup TF arms the signal, exec TF fills.

19 timeframe pairs (4h down to 1m) x strategy variants x cost scenarios.
Every run is appended to the backtest registry; the top runs get interactive
vectorbt HTML reports under reports/.

Usage:
    uv run python scripts/sweep_mtf_btcusdt.py
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from specula import runlog
from specula.backtest import build_portfolio, collect_metrics
from specula.sweeps import TF_PAIRS, pair_configs

SWEEP_TAG = "mtf-v1"


def run_pair(pair: tuple[str, str]) -> list[tuple[dict, dict]]:
    """Worker: run every config of one timeframe pair (signal caches stay hot)."""
    out = []
    for cfg in pair_configs(*pair):
        pf = build_portfolio(cfg)
        out.append((cfg, collect_metrics(pf)))
    return out


def make_report(cfg: dict, run_id: str, reports_dir: Path) -> Path:
    from specula.reporting import save_report

    return save_report(build_portfolio(cfg), cfg, run_id, reports_dir)


def main() -> int:
    t0 = time.monotonic()
    sha = runlog.git_sha()
    rows = []
    workers = min(len(TF_PAIRS), max(1, (os.cpu_count() or 4) - 2))
    total = sum(1 for p in TF_PAIRS for _ in pair_configs(*p))
    print(f"{total} configs across {len(TF_PAIRS)} timeframe pairs, "
          f"{workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for pair, pair_rows in zip(TF_PAIRS, ex.map(run_pair, TF_PAIRS)):
            rows += [runlog.make_row(cfg, m, SWEEP_TAG, sha) for cfg, m in pair_rows]
            print(f"  {pair[0]}->{pair[1]} done ({len(rows)}/{total}, "
                  f"{time.monotonic() - t0:.0f}s)", flush=True)

    registry = runlog.append(rows)
    result = pd.DataFrame(rows)
    print(f"\n{len(result)} runs in {(time.monotonic() - t0) / 60:.1f} min; "
          f"registry now holds {len(registry)} runs total", flush=True)

    viable = result[result["n_trades"] >= 30].copy()
    show = ["run_id", "strategy", "setup_tf", "exec_tf", "params", "n_trades",
            "win_rate_pct", "profit_factor", "avg_trade_pct", "total_return_pct",
            "max_dd_pct", "sharpe"]
    top = viable.sort_values("profit_factor", ascending=False).head(15)
    with pd.option_context("display.max_colwidth", 120, "display.width", 250):
        print("\n=== top 15 by profit_factor (n_trades >= 30) ===")
        print(top[show].to_string(index=False))

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    import json
    for _, r in top.head(6).iterrows():
        dest = make_report(json.loads(r["params"]), r["run_id"], reports_dir)
        print(f"report -> {dest}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
