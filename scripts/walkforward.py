"""Walk-forward validation over the full MTF config grid (BTCUSDT).

Method: every config is simulated once over the full history and reduced to its
trade table (entry time, return). Then rolling folds — train 120d, test 30d,
step 30d — pick the config with the best train-window profit factor (min 15
train trades) and collect its trades from the unseen test window. The stitched
out-of-sample trades are the honest performance estimate; the fee scenario is
fixed per pass (a cost assumption, not a strategy choice).

Writes data/meta/walkforward.json (+ mirror to web/public/data/) and prints a
summary.

Usage:
    uv run python scripts/walkforward.py
"""

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
from specula.sweeps import FEES, TF_PAIRS, cfg_label, pair_configs

TRAIN_DAYS = 120
TEST_DAYS = 30
STEP_DAYS = 30
MIN_TRAIN_TRADES = 15

OUT = Path("data/meta/walkforward.json")
WEB_OUT = Path("web/public/data/walkforward.json")


def trade_table(cfg: dict) -> pd.DataFrame:
    """One config -> closed trades with entry timestamp and return."""
    pf = build_portfolio(cfg)
    rec = pf.trades.records
    rec = rec[rec["status"] == 1]  # closed trades only
    idx = pf.wrapper.index
    return pd.DataFrame({
        "entry_ts": idx[rec["entry_idx"].to_numpy()],
        "ret": rec["return"].to_numpy(),
    })


def run_pair(pair: tuple[str, str]) -> list[tuple[dict, list, list]]:
    """Worker: (cfg, entry timestamps as ns ints, returns) per config of a pair."""
    out = []
    for cfg in pair_configs(*pair):
        t = trade_table(cfg)
        # pandas 3 defaults to datetime64[us] — force ns so the fold math holds
        ts_ns = t["entry_ts"].dt.as_unit("ns").astype("int64")
        out.append((cfg, ts_ns.tolist(), t["ret"].tolist()))
    return out


def profit_factor(rets: np.ndarray) -> float:
    wins = rets[rets > 0].sum()
    losses = -rets[rets < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def evaluate_scenario(candidates: list[dict], folds: list[dict]) -> dict:
    fold_rows = []
    oos = []  # stitched out-of-sample trades: (entry_ns, ret)
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

    oos.sort(key=lambda x: x[0])
    rets = np.array([r for _, r in oos])
    equity = np.cumprod(1 + rets) if len(rets) else np.array([])
    aggregate = {
        "oos_trades": int(len(rets)),
        "oos_pf": round(profit_factor(rets), 3) if len(rets) else None,
        "oos_win_rate_pct": round(100 * float((rets > 0).mean()), 1) if len(rets) else None,
        "oos_avg_trade_pct": round(100 * float(rets.mean()), 3) if len(rets) else None,
        "oos_return_pct": round(100 * (float(equity[-1]) - 1), 2) if len(rets) else None,
        "distinct_winners": len({r["winner"] for r in fold_rows if r.get("winner")}),
        "folds_with_winner": sum(1 for r in fold_rows if r.get("winner")),
        "folds_total": len(fold_rows),
    }
    curve = [
        {"t": str(pd.Timestamp(ts_ns).date()), "v": round(float(v), 4)}
        for (ts_ns, _), v in zip(oos, equity)
    ]
    return {"folds": fold_rows, "aggregate": aggregate, "equity": curve}


def main() -> int:
    t0 = time.monotonic()
    workers = min(len(TF_PAIRS), max(1, (os.cpu_count() or 4) - 2))
    print(f"simulating {sum(1 for p in TF_PAIRS for _ in pair_configs(*p))} configs "
          f"({workers} workers) ...", flush=True)

    all_configs = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for pair_out in ex.map(run_pair, TF_PAIRS):
            for cfg, ts_ns, rets in pair_out:
                all_configs.append({
                    "cfg": cfg,
                    "ts": np.array(ts_ns, dtype=np.int64),
                    "rets": np.array(rets),
                })
    print(f"trade tables ready ({time.monotonic() - t0:.0f}s)", flush=True)

    all_ts = np.concatenate([c["ts"] for c in all_configs if len(c["ts"])])
    start, end = all_ts.min(), all_ts.max()
    day_ns = 86_400 * 10**9
    folds = []
    cursor = start
    while cursor + (TRAIN_DAYS + TEST_DAYS) * day_ns <= end + day_ns:
        folds.append({
            "train_start": cursor,
            "train_end": cursor + TRAIN_DAYS * day_ns,
            "test_end": cursor + (TRAIN_DAYS + TEST_DAYS) * day_ns,
        })
        cursor += STEP_DAYS * day_ns

    scenarios = []
    for fee in FEES:
        cands = [c for c in all_configs if c["cfg"]["fee"] == fee]
        result = evaluate_scenario(cands, folds)
        scenarios.append({"fee": fee, **result})
        a = result["aggregate"]
        print(f"\nfee {fee * 100:.2f}%/side: OOS {a['oos_trades']} trades, "
              f"PF {a['oos_pf']}, win {a['oos_win_rate_pct']}%, "
              f"return {a['oos_return_pct']}% "
              f"({a['folds_with_winner']}/{a['folds_total']} folds, "
              f"{a['distinct_winners']} distinct winners)", flush=True)

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": "BTCUSDT",
        "method": {
            "train_days": TRAIN_DAYS, "test_days": TEST_DAYS, "step_days": STEP_DAYS,
            "min_train_trades": MIN_TRAIN_TRADES,
            "selection": "best train-window profit factor",
        },
        "scenarios": scenarios,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc), encoding="utf-8")
    if WEB_OUT.parent.parent.exists():
        WEB_OUT.parent.mkdir(parents=True, exist_ok=True)
        WEB_OUT.write_text(json.dumps(doc), encoding="utf-8")
    print(f"\nwrote {OUT} in {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
