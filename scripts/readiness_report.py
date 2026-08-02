"""Readiness report: advanced validation battery for the APPROVED setups.

For each approved setup (on its top league assets, stocks only):
  1. Month-by-month consistency (positive months, worst month)
  2. Block-bootstrap Monte Carlo on daily P&L -> drawdown/return
     distributions and probability of a losing year
  3. Cost stress: everything re-run at 2.5x fees — does the edge survive?
  4. Profit concentration: how much of the profit lives in the top 10 trades
  5. Verdict per setup against explicit criteria
Across setups:
  6. Daily P&L correlation matrix (near-duplicates = one bet, not many)
  7. Combined portfolio (all approved, fixed sizes): equity, max DD, PF

Writes reports/readiness-<date>.md and prints the summary.
Run: uv run python scripts/readiness_report.py [--assets-per-setup 12]
"""

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# validation reads the WHOLE lake — clear the discovery window the server
# environment sets for its job subprocesses
os.environ.pop("SPECULA_FRAME_START", None)

SIZE_USD = 1000.0  # per-class stock size


def pooled_pf(pnls):
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None
    return wins / losses


def max_drawdown(daily):
    cum = peak = dd = 0.0
    for p in daily:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd


def block_bootstrap(daily, n_sims=2000, block=5, rng=None):
    """Stationary-ish block bootstrap of the daily P&L sequence."""
    rng = rng or random.Random(7)
    vals = list(daily)
    if len(vals) < 2 * block:
        return None
    n = len(vals)
    totals, dds = [], []
    for _ in range(n_sims):
        seq = []
        while len(seq) < n:
            i = rng.randrange(n)
            seq.extend(vals[i:i + block])
        seq = seq[:n]
        totals.append(sum(seq))
        dds.append(max_drawdown(seq))
    totals.sort()
    dds.sort()
    q = lambda xs, p: xs[int(p * (len(xs) - 1))]
    return {
        "p05_total": q(totals, 0.05), "p50_total": q(totals, 0.50),
        "p95_total": q(totals, 0.95),
        "p05_dd": q(dds, 0.05),  # dds sorted ascending: p05 = worst tail
        "p50_dd": q(dds, 0.50),
        "p_loss": sum(1 for t in totals if t < 0) / len(totals),
    }


def setup_trades(params, symbols, fee):
    """(entry_ns, usd_pnl) per closed trade across the given symbols."""
    import gc

    from specula import backtest
    from specula.backtest import build_portfolio

    # multi-year frames are big — keep the caches bounded per call
    backtest._resample_cache.clear()
    backtest._signal_cache.clear()
    backtest._breakout_cache.clear()
    gc.collect()
    out = []
    for sym in symbols:
        cfg = {**params, "symbol": sym, "fee": fee}
        try:
            pf = build_portfolio(cfg)
            idx = pf.wrapper.index
            for r in pf.trades.records.to_dict("records"):
                if int(r["status"]) != 1:
                    continue
                out.append((int(idx[int(r["entry_idx"])].value),
                            float(r["return"]) * SIZE_USD))
        except Exception as e:
            print(f"  [warn] {sym}: {type(e).__name__}: {e}", flush=True)
    out.sort()
    return out


def daily_series(trades):
    d = defaultdict(float)
    for ns, usd in trades:
        day = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).date()
        d[day] += usd
    days = sorted(d)
    return days, [d[x] for x in days]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-per-setup", type=int, default=12)
    ap.add_argument("--stress-mult", type=float, default=2.5)
    args = ap.parse_args()

    from specula import league, runlog
    from specula.settings import get_settings
    from specula.sweeps import strategy_sig

    s = get_settings()
    fee = s["fee_stock_pct"] / 100.0
    approved = [f for f in runlog.fav_setups_list()
                if f["status"] == "approved" and f["params"]]
    if not approved:
        print("nothing approved")
        return
    store = league.load_store() or {}
    store_by_sig = {r["sig"]: r for r in store.get("configs", [])}
    cutoff = store.get("cutoff")
    cutoff_ns = int(datetime.fromisoformat(cutoff).timestamp() * 1e9)

    df = runlog.load()
    df = df[(df["sweep_tag"] == "setup-league-v1") & df["profit_factor"].notna()]
    rows_by_sig = defaultdict(list)
    for r in df.to_dict("records"):
        rows_by_sig[strategy_sig(json.loads(r["params"]))].append(r)

    results = []
    for f in approved:
        sig = f["sig"]
        label = f["label"]
        print(f"[readiness] {label}", flush=True)
        rows = sorted((r for r in rows_by_sig.get(sig, [])
                       if (r["n_trades"] or 0) >= 5),
                      key=lambda r: -(r["profit_factor"]
                                      if math.isfinite(r["profit_factor"]) else 0))
        symbols = [r["symbol"] for r in rows[:args.assets_per_setup]]
        if not symbols:
            print("  no league rows — run the league", flush=True)
            continue
        params = {k: v for k, v in f["params"].items()
                  if k not in ("symbol", "fee")}
        trades = setup_trades(params, symbols, fee)
        hold = [(ns, usd) for ns, usd in trades if ns >= cutoff_ns]

        # 1. monthly consistency
        months = defaultdict(list)
        for ns, usd in trades:
            months[datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                   .strftime("%Y-%m")].append(usd)
        mrows = sorted(months.items())
        pos_months = sum(1 for _, v in mrows if sum(v) > 0)
        worst_month = min((sum(v) for _, v in mrows), default=0)

        # 2. Monte Carlo on daily P&L
        _, daily = daily_series(trades)
        mc = block_bootstrap(daily)

        # 3. cost stress
        stress_trades = setup_trades(params, symbols[:8],
                                     fee * args.stress_mult)
        stress_hold = [u for ns, u in stress_trades if ns >= cutoff_ns]
        stress_pf = pooled_pf(stress_hold)

        # 4. concentration
        gross = sum(u for _, u in trades if u > 0)
        top10 = sum(sorted((u for _, u in trades if u > 0), reverse=True)[:10])
        conc = top10 / gross if gross else None

        st = store_by_sig.get(sig, {})
        train_pf = st.get("train_pf")
        hold_pf = st.get("hold_pf")
        checks = {
            "train_pf>=1.0": train_pf is not None and train_pf >= 1.0,
            "holdout_pf>=1.1": hold_pf is not None and hold_pf >= 1.1,
            "stress_hold_pf>=1.0": stress_pf is not None and stress_pf >= 1.0,
            "positive_months>=60%": mrows and pos_months / len(mrows) >= 0.6,
            "P(losing year)<=25%": mc is not None and mc["p_loss"] <= 0.25,
            "top10_trades<=35% of profit": conc is not None and conc <= 0.35,
        }
        verdict = ("READY" if all(checks.values())
                   else "NOT READY" if sum(not v for v in checks.values()) >= 2
                   else "BORDERLINE")
        results.append({
            "label": label, "sig": sig, "symbols": symbols,
            "n_trades": len(trades), "hold_trades": len(hold),
            "train_pf": train_pf, "hold_pf": hold_pf,
            "hold_pnl": round(sum(u for _, u in hold), 2),
            "months": len(mrows), "pos_months": pos_months,
            "worst_month": round(worst_month, 2),
            "mc": mc, "stress_pf": stress_pf, "conc": conc,
            "checks": checks, "verdict": verdict,
            "trades": trades,
        })

    # 6. correlation matrix + 7. combined portfolio
    all_days = sorted({d for r in results
                       for d in daily_series(r["trades"])[0]})
    series = {}
    for r in results:
        days, vals = daily_series(r["trades"])
        m = dict(zip(days, vals))
        series[r["label"]] = [m.get(d, 0.0) for d in all_days]

    def corr(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va == 0 or vb == 0:
            return None
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        return cov / (va * vb) ** 0.5

    labels = [r["label"] for r in results]
    combined = [sum(series[l][i] for l in labels)
                for i in range(len(all_days))]
    comb_dd = max_drawdown(combined)
    comb_mc = block_bootstrap(combined)

    # ---------------------------------------------------------------- report
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# Readiness report — approved setups ({now})", ""]
    lines.append(f"Holdout cutoff {cutoff[:10]} · {args.assets_per_setup} "
                 f"top assets/setup · ${SIZE_USD:.0f}/trade · fee stress "
                 f"×{args.stress_mult}")
    lines.append("")
    for r in results:
        mc = r["mc"] or {}
        lines += [
            f"## {r['verdict']} — {r['label']}", "",
            f"- trades {r['n_trades']} (holdout {r['hold_trades']}, "
            f"P&L ${r['hold_pnl']})",
            f"- train PF {r['train_pf']} · holdout PF {r['hold_pf']} · "
            f"stressed holdout PF {r['stress_pf'] and round(r['stress_pf'], 2)}",
            f"- months positive {r['pos_months']}/{r['months']} · worst "
            f"month ${r['worst_month']}",
            f"- Monte Carlo (1y): median ${mc.get('p50_total', 0):.0f}, "
            f"5% worst ${mc.get('p05_total', 0):.0f}, P(losing year) "
            f"{100 * mc.get('p_loss', 0):.0f}%, median max DD "
            f"${mc.get('p50_dd', 0):.0f}, 5% worst DD ${mc.get('p05_dd', 0):.0f}",
            f"- top-10 trades = {100 * (r['conc'] or 0):.0f}% of gross profit",
            "- checks: " + " · ".join(
                f"{'✅' if v else '❌'} {k}" for k, v in r["checks"].items()),
            "",
        ]
    lines += ["## Cross-setup daily P&L correlation", ""]
    header = "| |" + "|".join(f" S{i+1} " for i in range(len(labels))) + "|"
    lines.append(header)
    lines.append("|---|" + "---|" * len(labels))
    for i, li in enumerate(labels):
        cells = []
        for j in range(len(labels)):
            c = corr(series[li], series[labels[j]])
            cells.append(f" {c:.2f} " if c is not None else " — ")
        lines.append(f"| S{i+1} |" + "|".join(cells) + "|")
    lines.append("")
    for i, l in enumerate(labels):
        lines.append(f"- S{i+1}: {l}")
    cm = comb_mc or {}
    lines += [
        "", "## Combined portfolio (all approved, simultaneous)", "",
        f"- total P&L ${sum(combined):.0f} over {len(all_days)} trading days",
        f"- realized max DD ${comb_dd:.0f}",
        f"- Monte Carlo: median ${cm.get('p50_total', 0):.0f}, 5% worst "
        f"${cm.get('p05_total', 0):.0f}, P(losing year) "
        f"{100 * cm.get('p_loss', 0):.0f}%, 5% worst DD ${cm.get('p05_dd', 0):.0f}",
        "",
        "## Not yet tested (needs engine flags — future work)",
        "- entry-delay sensitivity (fill one bar late)",
        "- per-symbol measured slippage (IB tape feature request)",
        "- parameter-plateau scan around each approved config",
    ]
    out = Path(f"reports/readiness-{now}.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    # portal-readable JSON (the Setups page Readiness tab)
    matrix = [[corr(series[a], series[b]) for b in labels] for a in labels]
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cutoff": cutoff, "assets_per_setup": args.assets_per_setup,
        "stress_mult": args.stress_mult, "size_usd": SIZE_USD,
        "results": [{k: v for k, v in r.items() if k != "trades"}
                    for r in results],
        "corr": {"labels": labels, "matrix": matrix},
        "combined": {"pnl": round(sum(combined), 2), "days": len(all_days),
                     "max_dd": round(comb_dd, 2), "mc": comb_mc},
        "report_file": f"/reports/readiness-{now}.md",
    }
    Path("data/meta/readiness.json").write_text(
        json.dumps(doc), encoding="utf-8")

    print("\n" + "=" * 72)
    for r in results:
        print(f"{r['verdict']:<11} {r['label'][:80]}")
    print(f"\ncombined: P&L ${sum(combined):.0f}, max DD ${comb_dd:.0f}, "
          f"P(losing year) {100 * cm.get('p_loss', 0):.0f}%")
    print(f"report -> {out}", flush=True)


if __name__ == "__main__":
    main()
