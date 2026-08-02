"""Manage the autotrade roster: which assets get nightly reprocessing now,
and paper/live execution in Part C.

Eligibility discipline: enabling requires a positive out-of-sample verdict
(walk-forward PF > 1 at the lowest fee) unless --force is passed.

Usage:
    uv run python scripts/autotrade.py list
    uv run python scripts/autotrade.py enable LLY [--size 1000] [--force]
    uv run python scripts/autotrade.py disable LLY
"""

import argparse
import json
import sys
from pathlib import Path

from specula import runlog

WF = Path("data/meta/walkforward.json")


def oos_pf(symbol: str) -> float | None:
    if not WF.exists():
        return None
    wf = json.loads(WF.read_text(encoding="utf-8"))
    best = None
    for d in wf.get("symbols", []):
        if d["symbol"].split("·")[0] != symbol:
            continue
        for s in d["scenarios"][:1]:  # lowest fee
            pf = s["aggregate"].get("oos_pf")
            if pf is not None and (best is None or pf > best):
                best = pf
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    en = sub.add_parser("enable")
    en.add_argument("symbol")
    en.add_argument("--size", type=float, default=None, help="position size USD")
    en.add_argument("--force", action="store_true",
                    help="enable without a positive OOS verdict")
    dis = sub.add_parser("disable")
    dis.add_argument("symbol")
    args = ap.parse_args()

    if args.cmd == "list":
        rows = runlog.autotrade_list()
        if not rows:
            print("roster empty — nightly reprocessing covers the default symbols")
            return 0
        for r in rows:
            state = "ON " if r["enabled"] else "off"
            print(f"[{state}] {r['symbol']:>8}  size ${r['size_usd']:.0f}  "
                  f"(added {r['added_at']})")
        return 0

    sym = args.symbol.upper()
    if args.cmd == "disable":
        runlog.autotrade_set(sym, enabled=False)
        print(f"{sym} disabled")
        return 0

    pf = oos_pf(sym)
    if pf is None or pf <= 1.0:
        msg = (f"{sym} has no positive out-of-sample verdict "
               f"(OOS PF: {pf if pf is not None else 'none'})")
        if not args.force:
            print(f"refused: {msg}. Use --force to override.", file=sys.stderr)
            return 1
        print(f"warning: {msg} — enabling anyway (--force)")
    runlog.autotrade_set(sym, enabled=True, size_usd=args.size)
    print(f"{sym} enabled (OOS PF {pf})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
