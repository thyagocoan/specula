"""Rebuild any logged backtest by run_id and save its interactive report.

Every row in the registry stores the full config, so any historical run can be
reproduced and visualized on demand:

    uv run python scripts/plot_run.py <run_id> [<run_id> ...]
    uv run python scripts/plot_run.py --list          # show the registry
"""

import sys
from pathlib import Path

import pandas as pd

from specula import runlog
from specula.backtest import build_portfolio


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        df = runlog.load()
        cols = ["run_id", "created_at", "sweep_tag", "strategy", "setup_tf",
                "exec_tf", "n_trades", "profit_factor", "total_return_pct"]
        with pd.option_context("display.max_rows", 50, "display.width", 200):
            print(df[cols].sort_values("profit_factor", ascending=False).head(50).to_string(index=False))
        print(f"\n{len(df)} runs in registry")
        return 0

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    for run_id in args:
        cfg = runlog.get_cfg(run_id)
        pf = build_portfolio(cfg)
        try:
            fig = pf.plot()
        except Exception:
            fig = pf.value().vbt.plot()
        fig.update_layout(title=f"{cfg['strategy']} {cfg['setup_tf']}->{cfg['exec_tf']} | run {run_id}")
        dest = reports_dir / f"{run_id}.html"
        fig.write_html(dest)
        print(f"{run_id}: {cfg}\n  -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
