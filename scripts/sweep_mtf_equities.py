"""MTF sweep across the 10 S&P mega-caps (session-aligned bars, EOD flat).

Same grid as the BTCUSDT MTF sweep but with equity fee scenarios (spread-based)
and one task per (symbol, timeframe pair) so workers keep their caches hot.

Usage:
    uv run python scripts/sweep_mtf_equities.py
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from specula import runlog
from specula.backtest import build_portfolio, collect_metrics
from specula.data import EQUITY_SYMBOLS
from specula.sweeps import EQUITY_FEES, TF_PAIRS, pair_configs

SWEEP_TAG = "mtf-equities-v1"
SYMBOLS = sorted(EQUITY_SYMBOLS)


def run_task(task: tuple[str, tuple[str, str]]) -> list[tuple[dict, dict]]:
    symbol, pair = task
    out = []
    for cfg in pair_configs(*pair, symbol=symbol, fees=EQUITY_FEES):
        try:
            pf = build_portfolio(cfg)
            out.append((cfg, collect_metrics(pf)))
        except Exception as e:  # a bad symbol/pair combo must not sink the sweep
            print(f"[error] {symbol} {pair}: {type(e).__name__}: {e}", flush=True)
    return out


def main() -> int:
    t0 = time.monotonic()
    tasks = [(s, p) for s in SYMBOLS for p in TF_PAIRS]
    total = len(tasks) * sum(1 for _ in pair_configs(*TF_PAIRS[0], fees=EQUITY_FEES))
    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"{total} configs across {len(SYMBOLS)} symbols x {len(TF_PAIRS)} TF pairs, "
          f"{workers} workers", flush=True)

    rows = []
    sha = runlog.git_sha()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, task_rows in enumerate(ex.map(run_task, tasks), 1):
            rows += [runlog.make_row(cfg, m, SWEEP_TAG, sha) for cfg, m in task_rows]
            if i % 20 == 0:
                print(f"  {i}/{len(tasks)} tasks ({time.monotonic() - t0:.0f}s)", flush=True)

    registry = runlog.append(rows)
    result = pd.DataFrame(rows)
    print(f"\n{len(result)} runs in {(time.monotonic() - t0) / 60:.1f} min; "
          f"registry now {len(registry)}", flush=True)

    viable = result[result["n_trades"] >= 30]
    show = ["symbol", "strategy", "setup_tf", "exec_tf", "params", "n_trades",
            "win_rate_pct", "profit_factor", "total_return_pct", "max_dd_pct", "sharpe"]
    with pd.option_context("display.max_colwidth", 110, "display.width", 250):
        print("\n=== top 3 per symbol by profit_factor (n_trades >= 30) ===")
        top = (viable.sort_values("profit_factor", ascending=False)
               .groupby("symbol").head(3).sort_values(["symbol", "profit_factor"],
                                                      ascending=[True, False]))
        print(top[show].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
