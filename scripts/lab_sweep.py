"""Strategy-lab sweep for ORB / VWAP / RSI-cross: coarse -> score -> refine.

Stages:
  --stage coarse   grids on 5m exec over 20 crypto + top-50 stocks by dollar
                   volume (tag lab-coarse-v1)
  --score          plateau + cross-asset consistency over lab-ma-v1 +
                   lab-coarse-v1 registry rows -> data/meta/lab_candidates.json
  --stage refine   candidates re-run at 1m exec on their viable symbols
                   (tag lab-refine-v1)

Usage:
    uv run python scripts/lab_sweep.py --stage coarse [--limit N]
    uv run python scripts/lab_sweep.py --score
    uv run python scripts/lab_sweep.py --stage refine
"""

import argparse
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import polars as pl

from specula import runlog
from specula.backtest import build_portfolio, collect_metrics
from specula.data import equity_symbols, is_equity
from specula.sweeps import EQUITY_FEES, FEES

CANDIDATES = Path("data/meta/lab_candidates.json")
EXITS_R = [(0.005, 0.005), (0.005, 0.01), (0.01, 0.005), (0.01, 0.01)]
COARSE_EXEC = "5min"
REFINE_EXEC = "1min"
TOP_FAMILIES = 50
N_STOCKS_COARSE = 50


def fees_for(symbol: str) -> list[float]:
    return EQUITY_FEES if is_equity(symbol) else FEES


def top_stocks_by_dollar_volume(n: int = N_STOCKS_COARSE) -> list[str]:
    base = Path("data/silver/equity_1m_adjusted")
    scores = []
    for p in base.glob("symbol=*"):
        sym = p.name.split("=", 1)[1]
        try:
            df = pl.scan_parquet(p / "**" / "*.parquet").select(
                (pl.col("close") * pl.col("volume")).sum().alias("dv")
            ).collect()
            scores.append((sym, float(df["dv"][0])))
        except Exception:
            continue
    scores.sort(key=lambda x: -x[1])
    return [s for s, _ in scores[:n]]


def coarse_assets() -> list[str]:
    crypto = sorted(p.name for p in Path("data/raw/binance/spot").glob("*"))
    return crypto + top_stocks_by_dollar_volume()


def family_configs(symbol: str, exec_tf: str):
    fees = fees_for(symbol)
    for rng, (sl, tp), fee in itertools.product([15, 30, 60], EXITS_R, fees):
        yield dict(strategy="lab", symbol=symbol, setup_tf=exec_tf, exec_tf=exec_tf,
                   entry=dict(kind="orb", range_min=rng),
                   exit=dict(kind="fixed_r", sl=sl, tp=tp), fee=fee)
    for rng, mb, fee in itertools.product([30, 60], [24, 48], fees):
        yield dict(strategy="lab", symbol=symbol, setup_tf=exec_tf, exec_tf=exec_tf,
                   entry=dict(kind="orb", range_min=rng),
                   exit=dict(kind="time", max_bars=mb, sl=0.01), fee=fee)
    for mode, k, (sl, tp), fee in itertools.product(
        ["revert", "cross"], [1.0, 1.5, 2.0], EXITS_R, fees
    ):
        yield dict(strategy="lab", symbol=symbol, setup_tf=exec_tf, exec_tf=exec_tf,
                   entry=dict(kind="vwap", mode=mode, band_k=k),
                   exit=dict(kind="fixed_r", sl=sl, tp=tp), fee=fee)
    for stf, (lo, hi), (sl, tp), fee in itertools.product(
        ["30min", "1h"], [(30, 70), (20, 80)], EXITS_R, fees
    ):
        yield dict(strategy="lab", symbol=symbol, setup_tf=stf, exec_tf=exec_tf,
                   entry=dict(kind="rsi_cross", window=14, lo=lo, hi=hi),
                   exit=dict(kind="fixed_r", sl=sl, tp=tp), fee=fee)


def run_symbol(args_tuple) -> list[tuple[dict, dict]]:
    symbol, stage = args_tuple
    out = []
    if stage == "coarse":
        cfgs = list(family_configs(symbol, COARSE_EXEC))
    else:
        cand = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        cfgs = []
        for c in cand["candidates"]:
            if symbol not in c["symbols"]:
                continue
            for fee in fees_for(symbol):
                cfg = json.loads(json.dumps(c["cfg"]))
                cfg.update(symbol=symbol, exec_tf=REFINE_EXEC, fee=fee)
                cfgs.append(cfg)
    for cfg in cfgs:
        try:
            out.append((cfg, collect_metrics(build_portfolio(cfg))))
        except Exception as e:
            print(f"[error] {symbol} {cfg.get('entry')}: {type(e).__name__}: {e}",
                  flush=True)
    return out


def family_key(params: dict) -> str:
    """Setup identity: params minus symbol/fee (grouping key across assets)."""
    p = {k: v for k, v in params.items() if k not in ("symbol", "fee")}
    return json.dumps(p, sort_keys=True)


def score() -> None:
    df = runlog.load()
    df = df[df["sweep_tag"].isin(["lab-ma-v1", "lab-coarse-v1"])].copy()
    if df.empty:
        print("no lab rows to score yet", flush=True)
        return
    df["fkey"] = [family_key(json.loads(p)) for p in df["params"]]
    # per (family, symbol): use the lowest-fee row
    df["fee"] = [json.loads(p)["fee"] for p in df["params"]]
    df = df.sort_values("fee").groupby(["fkey", "symbol"], as_index=False).first()

    fams = []
    for fkey, g in df.groupby("fkey"):
        viable = g[(g["n_trades"] >= 30) & (g["profit_factor"] >= 1.1)]
        if len(viable) == 0:
            continue
        fams.append({
            "fkey": fkey,
            "n_viable": len(viable),
            "median_pf": float(viable["profit_factor"].median()),
            "symbols": sorted(viable["symbol"].tolist()),
        })
    fams.sort(key=lambda f: (-f["n_viable"], -f["median_pf"]))
    top = fams[:TOP_FAMILIES]

    candidates = []
    for f in top:
        cfg = json.loads(f["fkey"])
        candidates.append({
            "cfg": cfg,
            "symbols": f["symbols"],
            "n_viable": f["n_viable"],
            "median_pf": round(f["median_pf"], 3),
        })
    CANDIDATES.write_text(json.dumps({"candidates": candidates}, indent=1),
                          encoding="utf-8")
    print(f"scored {len(fams)} families -> kept {len(candidates)} "
          f"(best: {candidates[0]['n_viable']} assets, "
          f"median PF {candidates[0]['median_pf']})" if candidates else "none kept",
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["coarse", "refine"], default=None)
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.score:
        score()
        return 0
    if args.stage is None:
        print("nothing to do: pass --stage or --score", file=sys.stderr)
        return 1

    t0 = time.monotonic()
    if args.stage == "coarse":
        symbols = coarse_assets()
        tag = "lab-coarse-v1"
    else:
        cand = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        symbols = sorted({s for c in cand["candidates"] for s in c["symbols"]})
        tag = "lab-refine-v1"
    if args.limit:
        symbols = symbols[: args.limit]

    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"lab {args.stage}: {len(symbols)} symbols, {workers} workers", flush=True)
    sha = runlog.git_sha()
    rows = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for symbol, out in zip(symbols,
                               ex.map(run_symbol, [(s, args.stage) for s in symbols])):
            rows += [runlog.make_row(cfg, m, tag, sha) for cfg, m in out]
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(symbols)} symbols "
                      f"({time.monotonic() - t0:.0f}s)", flush=True)
    if rows:
        runlog.append(rows)
    print(f"{args.stage}: {len(rows)} runs logged in "
          f"{(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
