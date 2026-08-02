"""Walk-forward core: trade tables, rolling folds, per-fold selection, OOS
aggregation. Shared by scripts/walkforward.py and validation experiments."""

import numpy as np
import pandas as pd

from specula.backtest import build_portfolio

TRAIN_DAYS = 120
TEST_DAYS = 30
STEP_DAYS = 30
MIN_TRAIN_TRADES = 15
DAY_NS = 86_400 * 10**9


def trade_table(cfg: dict) -> pd.DataFrame:
    """One config -> closed trades with ns entry timestamps and returns."""
    pf = build_portfolio(cfg)
    rec = pf.trades.records
    rec = rec[rec["status"] == 1]
    idx = pf.wrapper.index
    t = pd.DataFrame({
        "entry_ts": idx[rec["entry_idx"].to_numpy()],
        "ret": rec["return"].to_numpy(),
    })
    t["ts_ns"] = t["entry_ts"].dt.as_unit("ns").astype("int64")
    return t


def candidate(cfg: dict) -> dict:
    t = trade_table(cfg)
    return {"cfg": cfg, "ts": t["ts_ns"].to_numpy(), "rets": t["ret"].to_numpy()}


def profit_factor(rets: np.ndarray) -> float:
    wins = rets[rets > 0].sum()
    losses = -rets[rets < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def make_folds(start: int, end: int, train_days: int = TRAIN_DAYS,
               test_days: int = TEST_DAYS, step_days: int = STEP_DAYS) -> list[dict]:
    folds = []
    cursor = start
    while cursor + (train_days + test_days) * DAY_NS <= end + DAY_NS:
        folds.append({
            "train_start": cursor,
            "train_end": cursor + train_days * DAY_NS,
            "test_end": cursor + (train_days + test_days) * DAY_NS,
        })
        cursor += step_days * DAY_NS
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


def evaluate_scenario(candidates: list[dict], folds: list[dict], label_fn,
                      min_train_trades: int = MIN_TRAIN_TRADES) -> dict:
    fold_rows = []
    oos: list[tuple[int, float]] = []
    for f in folds:
        best = None
        for c in candidates:
            ts, rets = c["ts"], c["rets"]
            mask = (ts >= f["train_start"]) & (ts < f["train_end"])
            if mask.sum() < min_train_trades:
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
            "winner": label_fn(best["cfg"]),
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
