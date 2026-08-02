"""Walk-forward validation over the full MTF config grid, multi-symbol.

Method: every config is simulated once over the full history and reduced to its
trade table (entry time, return). Then rolling folds — train 120d, test 30d,
step 30d — pick the config with the best train-window profit factor (min 15
train trades) and collect its trades from the unseen test window. The stitched
out-of-sample trades are the honest performance estimate; the fee scenario is
fixed per pass (a cost assumption, not a strategy choice). Fee levels are per
asset class (crypto: taker fees; equities: spread-based).

An ALL-EQUITIES pseudo-symbol aggregates the stitched OOS trades of every
equity symbol per fee level — the portfolio view of trading the whole book.

Writes data/meta/walkforward.json (+ mirror to web/public/data/).

Usage:
    uv run python scripts/walkforward.py [--symbols BTCUSDT,NVDA,...]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from specula.backtest import build_portfolio
from specula.data import EQUITY_SYMBOLS, is_equity
from specula.sweeps import EQUITY_FEES, FEES, TF_PAIRS, cfg_label, pair_configs

TRAIN_DAYS = 120
TEST_DAYS = 30
STEP_DAYS = 30
MIN_TRAIN_TRADES = 15
DAY_NS = 86_400 * 10**9

OUT = Path("data/meta/walkforward.json")
WEB_OUT = Path("web/public/data/walkforward.json")

DEFAULT_SYMBOLS = ["BTCUSDT"] + sorted(EQUITY_SYMBOLS)


def fees_for(symbol: str) -> list[float]:
    return EQUITY_FEES if is_equity(symbol) else FEES


def trade_table(cfg: dict) -> pd.DataFrame:
    pf = build_portfolio(cfg)
    rec = pf.trades.records
    rec = rec[rec["status"] == 1]  # closed trades only
    idx = pf.wrapper.index
    return pd.DataFrame({
        "entry_ts": idx[rec["entry_idx"].to_numpy()],
        "ret": rec["return"].to_numpy(),
    })


def run_task(task: tuple[str, list[dict]]) -> list[tuple[dict, list, list]]:
    symbol, cfgs = task
    out = []
    for cfg in cfgs:
        try:
            t = trade_table(cfg)
            ts_ns = t["entry_ts"].dt.as_unit("ns").astype("int64")
            out.append((cfg, ts_ns.tolist(), t["ret"].tolist()))
        except Exception as e:
            print(f"[error] {symbol}: {type(e).__name__}: {e}", flush=True)
    return out


def profit_factor(rets: np.ndarray) -> float:
    wins = rets[rets > 0].sum()
    losses = -rets[rets < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def make_folds(start: int, end: int) -> list[dict]:
    folds = []
    cursor = start
    while cursor + (TRAIN_DAYS + TEST_DAYS) * DAY_NS <= end + DAY_NS:
        folds.append({
            "train_start": cursor,
            "train_end": cursor + TRAIN_DAYS * DAY_NS,
            "test_end": cursor + (TRAIN_DAYS + TEST_DAYS) * DAY_NS,
        })
        cursor += STEP_DAYS * DAY_NS
    return folds


def aggregate_oos(oos: list[tuple[int, float]]) -> tuple[dict, list]:
    oos = sorted(oos, key=lambda x: x[0])
    rets = np.array([r for _, r in oos])
    equity = np.cumprod(1 + rets) if len(rets) else np.array([])
    agg = {
        "oos_trades": int(len(rets)),
        "oos_pf": round(profit_factor(rets), 3) if len(rets) else None,
        "oos_win_rate_pct": round(100 * float((rets > 0).mean()), 1) if len(rets) else None,
        "oos_avg_trade_pct": round(100 * float(rets.mean()), 3) if len(rets) else None,
        "oos_return_pct": round(100 * (float(equity[-1]) - 1), 2) if len(rets) else None,
    }
    curve = [
        {"t": str(pd.Timestamp(ts).date()), "v": round(float(v), 4)}
        for (ts, _), v in zip(oos, equity)
    ]
    return agg, curve


def evaluate_scenario(candidates: list[dict], folds: list[dict]) -> dict:
    fold_rows = []
    oos: list[tuple[int, float]] = []
    for f in folds:
        best = None
        for c in candidates:
            ts, rets = c["ts"], c["rets"]
            mask = (ts >= f["train_start"]) & (ts < f["train_end"])
            if mask.sum() < MIN_TRAIN_TRADES:
                continue
            pf_train = profit_factor(rets[mask])
            score = (pf_train, rets[mask].mean())
            if best is None or score > best["score"]:
                best = {"cfg": c["cfg"], "score": score, "pf": pf_train,
                        "n": int(mask.sum()), "ts": ts, "rets": rets}
        row = {
            "train_start": str(pd.Timestamp(f["train_start"]).date()),
            "train_end": str(pd.Timestamp(f["train_end"]).date()),
            "test_end": str(pd.Timestamp(f["test_end"]).date()),
        }
        if best is None:
            fold_rows.append({**row, "winner": None})
            continue
        tmask = (best["ts"] >= f["train_end"]) & (best["ts"] < f["test_end"])
        test_rets = best["rets"][tmask]
        oos += list(zip(best["ts"][tmask].tolist(), test_rets.tolist()))
        fold_rows.append({
            **row,
            "winner": cfg_label(best["cfg"]),
            "winner_params": best["cfg"],
            "train_pf": round(best["pf"], 3) if np.isfinite(best["pf"]) else None,
            "train_trades": best["n"],
            "test_trades": int(tmask.sum()),
            "test_pf": round(profit_factor(test_rets), 3) if len(test_rets) else None,
            "test_return_pct": round(100 * (np.prod(1 + test_rets) - 1), 2),
            "test_win_rate_pct": round(100 * float((test_rets > 0).mean()), 1)
            if len(test_rets) else None,
        })

    agg, curve = aggregate_oos(oos)
    agg["distinct_winners"] = len({r["winner"] for r in fold_rows if r.get("winner")})
    agg["folds_with_winner"] = sum(1 for r in fold_rows if r.get("winner"))
    agg["folds_total"] = len(fold_rows)
    return {"folds": fold_rows, "aggregate": agg, "equity": curve, "oos_raw": oos}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="",
                    help="comma list; default = core symbols (grid mode) or "
                         "all candidate symbols (candidates mode)")
    ap.add_argument("--candidates", default=None,
                    help="lab_candidates.json — evaluate these cfgs instead of "
                         "the built-in grid; results keyed '<symbol>·lab'")
    args = ap.parse_args()
    sym_filter = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    t0 = time.monotonic()
    label_suffix = ""
    if args.candidates:
        cand = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        sym_cfgs: dict[str, list[dict]] = {}
        for c in cand["candidates"]:
            for s in c["symbols"]:
                if sym_filter and s.upper() not in sym_filter:
                    continue
                for fee in fees_for(s):
                    cfg = json.loads(json.dumps(c["cfg"]))
                    cfg.update(symbol=s, exec_tf="1min", fee=fee)
                    sym_cfgs.setdefault(s, []).append(cfg)
        symbols = sorted(sym_cfgs)
        tasks = []
        for s in symbols:
            cfgs = sym_cfgs[s]
            tasks += [(s, cfgs[i:i + 24]) for i in range(0, len(cfgs), 24)]
        label_suffix = "·lab"
        print(f"candidates mode: {len(symbols)} symbols, "
              f"{sum(len(v) for v in sym_cfgs.values())} cfgs", flush=True)
    else:
        symbols = sym_filter or DEFAULT_SYMBOLS
        tasks = [(s, list(pair_configs(*p, symbol=s, fees=fees_for(s))))
                 for s in symbols for p in TF_PAIRS]
    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"{len(symbols)} symbols, {len(tasks)} tasks, {workers} workers",
          flush=True)

    by_symbol: dict[str, list[dict]] = {s: [] for s in symbols}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for (symbol, _), task_out in zip(tasks, ex.map(run_task, tasks)):
            for cfg, ts_ns, rets in task_out:
                by_symbol[symbol].append({
                    "cfg": cfg,
                    "ts": np.array(ts_ns, dtype=np.int64),
                    "rets": np.array(rets),
                })
    print(f"trade tables ready ({time.monotonic() - t0:.0f}s)", flush=True)

    symbol_docs = []
    equity_oos: dict[int, list] = {}  # fee level index -> stitched OOS trades
    for symbol in symbols:
        configs = by_symbol[symbol]
        nonempty = [c["ts"] for c in configs if len(c["ts"])]
        if not nonempty:
            print(f"[skip] {symbol}: no trades at all", flush=True)
            continue
        all_ts = np.concatenate(nonempty)
        folds = make_folds(all_ts.min(), all_ts.max())
        scenarios = []
        for fi, fee in enumerate(fees_for(symbol)):
            cands = [c for c in configs if c["cfg"]["fee"] == fee]
            result = evaluate_scenario(cands, folds)
            oos_raw = result.pop("oos_raw")
            if is_equity(symbol):
                equity_oos.setdefault(fi, []).extend(oos_raw)
            scenarios.append({"fee": fee, **result})
            a = result["aggregate"]
            print(f"{symbol}{label_suffix} fee {fee * 100:.2f}%: "
                  f"OOS {a['oos_trades']} trades, "
                  f"PF {a['oos_pf']}, win {a['oos_win_rate_pct']}%, "
                  f"return {a['oos_return_pct']}%", flush=True)
        symbol_docs.append({"symbol": symbol + label_suffix, "scenarios": scenarios})

    # portfolio view: all equity symbols' OOS trades stitched together
    if equity_oos:
        scenarios = []
        for fi, fee in enumerate(EQUITY_FEES):
            agg, curve = aggregate_oos(equity_oos.get(fi, []))
            scenarios.append({"fee": fee, "folds": [], "aggregate": agg,
                              "equity": curve})
            print(f"ALL-EQUITIES{label_suffix} fee {fee * 100:.2f}%: "
                  f"OOS {agg['oos_trades']} trades, "
                  f"PF {agg['oos_pf']}, return {agg['oos_return_pct']}%", flush=True)
        symbol_docs.append({"symbol": "ALL-EQUITIES" + label_suffix,
                            "scenarios": scenarios})

    # merge with existing results: replace docs we re-evaluated, keep the rest
    existing = []
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8")).get("symbols", [])
        except Exception:
            existing = []
    new_keys = {d["symbol"] for d in symbol_docs}
    symbol_docs = [d for d in existing if d["symbol"] not in new_keys] + symbol_docs

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {
            "train_days": TRAIN_DAYS, "test_days": TEST_DAYS, "step_days": STEP_DAYS,
            "min_train_trades": MIN_TRAIN_TRADES,
            "selection": "best train-window profit factor",
        },
        "symbols": symbol_docs,
    }
    def _json_safe(o):
        if isinstance(o, dict):
            return {k: _json_safe(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_json_safe(v) for v in o]
        if isinstance(o, float) and not np.isfinite(o):
            return None
        return o

    doc = _json_safe(doc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc), encoding="utf-8")
    if WEB_OUT.parent.parent.exists():
        WEB_OUT.parent.mkdir(parents=True, exist_ok=True)
        WEB_OUT.write_text(json.dumps(doc), encoding="utf-8")
    print(f"\nwrote {OUT} in {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
