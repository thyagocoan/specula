"""Backfill the equity lake to N years of 1-minute history (default 8).

Downloads split/dividend-adjusted Alpaca bars for every stock in the lake
(idempotent — existing months are skipped) and rebuilds bronze/silver.
The discovery pipeline stays on its 13-month window (SPECULA_FRAME_START);
the deep history exists for the readiness validation battery.

Honesty note: the universe is TODAY's top-250 by market cap, so multi-year
results carry survivorship bias — fine for confirming setup robustness,
optimistic for absolute returns.

Run: uv run python scripts/backfill_equities.py [--years 8]
"""

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=8)
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    from specula.data import equity_symbols

    symbols = sorted(equity_symbols())
    today = date.today()
    start = f"{today.year - args.years}-{today.month:02d}"
    print(f"[backfill] {len(symbols)} symbols from {start} "
          f"(adjusted bars only)", flush=True)

    if not args.skip_download:
        t0 = time.time()
        r = subprocess.run([
            sys.executable, "scripts/download_alpaca_raw.py",
            "--start", start, "--symbols", ",".join(symbols),
            "--adjustments", "all",
        ])
        print(f"[backfill] download rc={r.returncode} "
              f"({(time.time() - t0) / 3600:.1f} h)", flush=True)
        if r.returncode != 0:
            return 1

    t0 = time.time()
    r = subprocess.run([sys.executable, "scripts/build_bronze_alpaca.py"])
    print(f"[backfill] bronze/silver rc={r.returncode} "
          f"({(time.time() - t0) / 60:.0f} min)", flush=True)
    print("[backfill] done — run the readiness report for full-history "
          "validation", flush=True)
    return r.returncode


if __name__ == "__main__":
    main()
