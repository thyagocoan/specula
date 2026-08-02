"""Overnight strategy-discovery orchestrator.

Sequential steps, each with a wall-clock cap; a failed or timed-out step is
logged and the pipeline continues (later steps degrade gracefully — e.g.
scoring works with whatever sweeps finished).

    0. equity bronze/silver build + quality report (if new raw data present)
    1. MA-crossover megasweep (columnar, all symbols)         cap 3h
    2. lab coarse sweep (ORB/VWAP/RSI, 70 assets)             cap 2h
    3. scoring -> lab_candidates.json
    4. lab refine (candidates at 1min, all their assets)      cap 1.5h
    5. walk-forward on candidates                             cap 1h
    6. exports (curves + web)

Usage:
    uv run python scripts/overnight_lab.py
"""

import subprocess
import sys
import time
from pathlib import Path

STEPS = [
    ("equity bronze/silver", ["scripts/build_bronze_alpaca.py"], 3600),
    ("quality report", ["scripts/quality_report.py"], 1800),
    ("MA megasweep", ["scripts/megasweep_ma.py"], 3 * 3600),
    ("lab coarse", ["scripts/lab_sweep.py", "--stage", "coarse"], 2 * 3600),
    ("scoring", ["scripts/lab_sweep.py", "--score"], 900),
    ("lab refine", ["scripts/lab_sweep.py", "--stage", "refine"], int(1.5 * 3600)),
    ("walk-forward (candidates)",
     ["scripts/walkforward.py", "--candidates", "data/meta/lab_candidates.json"],
     3600),
    ("equity curves", ["scripts/export_curves.py"], 1800),
    ("web export", ["scripts/export_web_data.py"], 600),
]


def main() -> int:
    t0 = time.monotonic()
    failures = 0
    for label, cmd, cap in STEPS:
        if "refine" in label or "candidates" in cmd[-1]:
            if not Path("data/meta/lab_candidates.json").exists():
                print(f"[skip] {label}: no candidates file", flush=True)
                continue
        t = time.monotonic()
        print(f"\n===== {label} (cap {cap // 60} min) =====", flush=True)
        try:
            r = subprocess.run([sys.executable, *cmd], timeout=cap)
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
            print(f"[timeout] {label} exceeded {cap // 60} min — moving on",
                  flush=True)
        failures += not ok
        print(f"[{'ok' if ok else 'FAIL'}] {label} "
              f"({(time.monotonic() - t) / 60:.1f} min, "
              f"total {(time.monotonic() - t0) / 60:.0f} min)", flush=True)

    print(f"\novernight lab finished in {(time.monotonic() - t0) / 3600:.1f} h, "
          f"{failures} failed step(s)", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
