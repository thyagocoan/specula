"""Nightly incremental update: fresh bars -> bronze/silver -> results -> portal.

Steps (each continues past individual failures, exit code reflects overall):
  1. Crypto: pull new daily archives for every symbol already in the lake
     (idempotent; monthly archives replace dailies as they publish), rebuild
     bronze per symbol.
  2. Equities: refetch current+previous month for every symbol in the lake,
     rebuild bronze/silver.
  3. Quality report.
  4. --with-backtests: walk-forward refresh (fast, updates the OOS verdicts).
     Full sweeps stay manual/portal-launched — rerunning the whole grid daily
     would bloat the registry without adding knowledge.
  5. Web data export.

Scheduled via Windows Task Scheduler (see repo README); also launchable from
the portal's Execute page.

Usage:
    uv run python scripts/daily_update.py [--with-backtests]
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

DATA = Path("data")


def crypto_symbols() -> list[str]:
    base = DATA / "raw" / "binance" / "spot"
    return sorted(p.name for p in base.glob("*")) if base.exists() else []


def equity_syms() -> list[str]:
    base = DATA / "raw" / "alpaca" / "stocks" / "1min" / "adjustment=raw"
    return sorted(p.name.split("=", 1)[1] for p in base.glob("symbol=*")) if base.exists() else []


def run(label: str, cmd: list[str]) -> bool:
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, *cmd])
    ok = r.returncode == 0
    print(f"[{'ok' if ok else 'FAIL'}] {label} ({time.monotonic() - t0:.0f}s)", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-backtests", action="store_true")
    args = ap.parse_args()

    failures = 0

    for sym in crypto_symbols():
        failures += not run(f"binance {sym}",
                            ["scripts/download_binance_raw.py", "--symbol", sym])
        failures += not run(f"bronze {sym}",
                            ["scripts/build_bronze_binance.py", "--symbol", sym])

    eq = equity_syms()
    if eq:
        failures += not run(f"alpaca {len(eq)} symbols",
                            ["scripts/download_alpaca_raw.py", "--symbols", ",".join(eq)])
        failures += not run("equity bronze/silver", ["scripts/build_bronze_alpaca.py"])

    failures += not run("quality report", ["scripts/quality_report.py"])

    if args.with_backtests:
        failures += not run("walk-forward", ["scripts/walkforward.py"])

    failures += not run("web export", ["scripts/export_web_data.py"])

    print(f"\ndaily update finished, {failures} failed step(s)", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
