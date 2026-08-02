"""Setup League — validate candidate setups across the whole universe.

Pipeline:
1. Candidates = favourite setups (portal stars, fav_setups table) + auto
   candidates from the registry (configs logged on many assets with a good
   median in-sample PF).
2. Every candidate runs on EVERY asset in the lake at the user's venue fees
   and per-class trade sizes.
3. Trades split at a holdout cutoff (default: the last 60 days). The configs
   were discovered on earlier data, so only the holdout column is trusted
   for approval — that is the League's whole point.
4. Ranked scorecard written to data/meta/setup_league.json; approve the top
   setups from the portal's League page.

Run: uv run python scripts/setup_league.py [--holdout-days 60]
     [--max-configs 40] [--symbols CSX,LLY] [--workers 8]
"""

import argparse
import hashlib
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OUT = Path("data/meta/setup_league.json")

DROP_KEYS = {"symbol", "fee"}


def _sig(params: dict) -> str:
    from specula.sweeps import strategy_sig

    return strategy_sig(params)


def _label(params: dict) -> str:
    from specula.sweeps import cfg_label

    try:
        return cfg_label(params)
    except Exception:
        return params.get("strategy", "?")


def candidate_configs(max_configs: int, min_assets: int) -> list[dict]:
    from specula import runlog

    out: dict[str, dict] = {}
    for f in runlog.fav_setups_list():
        if f["params"]:
            out[_sig(f["params"])] = {
                "sig": _sig(f["params"]),
                "label": f["label"] or _label(f["params"]),
                "params": {k: v for k, v in f["params"].items()
                           if k not in DROP_KEYS},
                "source": "favourite",
            }
    n_favs = len(out)

    df = runlog.load()
    df = df[df["profit_factor"].notna() & (df["n_trades"] >= 30)]
    stats: dict[str, dict] = {}
    for r in df.to_dict("records"):
        params = json.loads(r["params"])
        sig = _sig(params)
        st = stats.setdefault(sig, {"pfs": [], "symbols": set(),
                                    "params": params})
        st["pfs"].append(r["profit_factor"])
        st["symbols"].add(r["symbol"])
    ranked = []
    for sig, st in stats.items():
        if sig in out or len(st["symbols"]) < min_assets:
            continue
        pfs = sorted(st["pfs"])
        med = pfs[len(pfs) // 2]
        if med > 1.05:
            ranked.append((med, sig, st))
    ranked.sort(reverse=True, key=lambda x: x[0])
    for med, sig, st in ranked[: max(0, max_configs - len(out))]:
        params = {k: v for k, v in st["params"].items() if k not in DROP_KEYS}
        out[sig] = {"sig": sig, "label": _label(st["params"]),
                    "params": params, "source": f"auto (median PF {med:.2f})"}
    print(f"[league] candidates: {n_favs} favourites + "
          f"{len(out) - n_favs} auto = {len(out)}", flush=True)
    return list(out.values())


def universe() -> list[str]:
    from specula.data import DATA_ROOT, equity_symbols

    crypto_base = (DATA_ROOT / "bronze" / "crypto" / "exchange=binance"
                   / "market=spot")
    crypto = ({p.name.split("=", 1)[1] for p in crypto_base.glob("symbol=*")}
              if crypto_base.exists() else set())
    return sorted(equity_symbols() | crypto)


def run_symbol(symbol: str, configs: list[dict], fees: dict) -> dict:
    """One worker task: every candidate on one symbol (frames cached once).
    Returns per config: the trade list AND full metrics for the registry."""
    from specula.backtest import build_portfolio, collect_metrics
    from specula.data import is_equity

    fee = fees["stock"] if is_equity(symbol) else fees["crypto"]
    res = {}
    for c in configs:
        cfg = {**c["params"], "symbol": symbol, "fee": fee}
        try:
            pf = build_portfolio(cfg)
            idx = pf.wrapper.index
            trades = []
            for r in pf.trades.records.to_dict("records"):
                if int(r["status"]) != 1:
                    continue
                ts = idx[int(r["entry_idx"])]
                trades.append((int(ts.value), float(r["return"])))
            res[c["sig"]] = {"trades": trades, "cfg": cfg,
                             "metrics": collect_metrics(pf)}
        except Exception as e:
            res[c["sig"]] = None
            print(f"[league] {symbol} {c['label']}: "
                  f"{type(e).__name__}: {e}", flush=True)
    return res


def pooled_pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None if wins == 0 else math.inf
    return wins / losses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-days", type=int, default=60)
    ap.add_argument("--max-configs", type=int, default=40)
    ap.add_argument("--min-assets", type=int, default=8)
    ap.add_argument("--symbols", default=None,
                    help="comma list to restrict (testing)")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    import os

    from specula.data import is_equity
    from specula.settings import get_settings

    s = get_settings()
    fees = {"stock": s["fee_stock_pct"] / 100.0,
            "crypto": s["fee_crypto_pct"] / 100.0}
    sizes = {"stock": s["trade_size_stock_usd"] or 1000.0,
             "crypto": s["trade_size_crypto_usd"] or 100.0}

    configs = candidate_configs(args.max_configs, args.min_assets)
    if not configs:
        print("[league] no candidates — star some setups first", flush=True)
        return
    symbols = universe()
    if args.symbols:
        keep = {x.strip().upper() for x in args.symbols.split(",")}
        symbols = [x for x in symbols if x in keep]
    workers = args.workers or max(1, min(8, (os.cpu_count() or 8) - 2))
    print(f"[league] {len(configs)} configs x {len(symbols)} assets, "
          f"{workers} workers, holdout {args.holdout_days}d", flush=True)

    from specula import runlog

    t0 = time.time()
    sha = runlog.git_sha()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # sig -> symbol -> [(entry_ns, ret)]
    results: dict[str, dict[str, list]] = {c["sig"]: {} for c in configs}
    reg_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_symbol, sym, configs, fees): sym
                for sym in symbols}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            done += 1
            try:
                for sig, payload in fut.result().items():
                    if payload is None:
                        continue
                    results[sig][sym] = payload["trades"]
                    cfg = payload["cfg"]
                    # per-asset registry row with a DETERMINISTIC id, so a
                    # league re-run replaces instead of duplicating — this is
                    # what makes approved setups visible on every asset in
                    # the portal (StrategyBoard, Setups, asset reviews)
                    rid = hashlib.md5(
                        f"league|{sig}|{sym}|{cfg['fee']}".encode()
                    ).hexdigest()[:12]
                    reg_rows.append({
                        "run_id": rid, "created_at": now, "git_sha": sha,
                        "sweep_tag": "setup-league-v1", "symbol": sym,
                        "strategy": cfg.get("strategy"),
                        "setup_tf": cfg.get("setup_tf"),
                        "exec_tf": cfg.get("exec_tf"),
                        "params": json.dumps(cfg, sort_keys=True),
                        **payload["metrics"],
                    })
            except Exception as e:
                print(f"[league] {sym} failed: {type(e).__name__}: {e}",
                      flush=True)
            if done % 10 == 0 or done == len(symbols):
                print(f"[league] step assets {done}/{len(symbols)} "
                      f"({(time.time() - t0) / 60:.1f} min)", flush=True)

    if reg_rows:
        runlog.append(reg_rows)
        print(f"[league] logged {len(reg_rows)} per-asset runs to the "
              f"registry (tag setup-league-v1)", flush=True)

    max_ns = max((t[0] for per in results.values() for tr in per.values()
                  for t in tr), default=None)
    if max_ns is None:
        print("[league] no trades at all", flush=True)
        return
    cutoff_ns = max_ns - args.holdout_days * 86_400 * 10 ** 9

    # eligibility floors: crypto's universe is ~20 symbols, stocks ~250
    MIN_ASSETS = {"all": 5, "stock": 5, "crypto": 3}

    def aggregate(items: list[tuple[str, list]], cls: str) -> dict:
        train, hold = [], []
        asset_hold: dict[str, list] = {}
        n_assets = 0
        for sym, trades in items:
            n_assets += 1
            size = sizes["stock"] if is_equity(sym) else sizes["crypto"]
            for ns, ret in trades:
                usd = ret * size
                if ns >= cutoff_ns:
                    hold.append(usd)
                    asset_hold.setdefault(sym, []).append(usd)
                else:
                    train.append(usd)
        hold_assets = {k: v for k, v in asset_hold.items() if len(v) >= 3}
        good = [k for k, v in hold_assets.items()
                if (pf := pooled_pf(v)) is not None and pf > 1]
        d = {
            "assets_logged": n_assets,
            "train_trades": len(train),
            "train_pf": pooled_pf(train),
            "train_pnl_usd": round(sum(train), 2),
            "hold_trades": len(hold),
            "hold_pf": pooled_pf(hold),
            "hold_pnl_usd": round(sum(hold), 2),
            "hold_assets": len(hold_assets),
            "hold_assets_pf_gt1": len(good),
        }
        d["eligible"] = (d["hold_trades"] >= 30
                         and d["hold_assets"] >= MIN_ASSETS[cls])
        return d

    rows = []
    for c in configs:
        per = list(results[c["sig"]].items())
        stock_items = [(s, t) for s, t in per if is_equity(s)]
        crypto_items = [(s, t) for s, t in per if not is_equity(s)]
        row = {
            "sig": c["sig"], "label": c["label"], "source": c["source"],
            "params": c["params"],
            **aggregate(per, "all"),
            "classes": {
                "stock": aggregate(stock_items, "stock"),
                "crypto": aggregate(crypto_items, "crypto"),
            },
        }
        rows.append(row)

    def rank(get, put) -> list[dict]:
        eligible = [r for r in rows
                    if get(r)["eligible"] and get(r)["hold_pf"] is not None
                    and math.isfinite(get(r)["hold_pf"])]
        eligible.sort(key=lambda r: get(r)["hold_pf"], reverse=True)
        for i, r in enumerate(eligible):
            put(r, i + 1)
        return eligible

    eligible = rank(lambda r: r, lambda r, i: r.update(rank=i))
    for cls in ("stock", "crypto"):
        rank(lambda r, c=cls: r["classes"][c],
             lambda r, i, c=cls: r["classes"][c].update(rank=i))

    def _safe(v):
        if isinstance(v, dict):
            return {k: _safe(x) for k, x in v.items()}
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holdout_days": args.holdout_days,
        "cutoff": datetime.fromtimestamp(cutoff_ns / 1e9, tz=timezone.utc)
                  .isoformat(timespec="seconds"),
        "settings": s,
        "n_symbols": len(symbols),
        "configs": [{k: _safe(v) for k, v in r.items()} for r in rows],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc), encoding="utf-8")
    print(f"[league] wrote {OUT} — top of the table:", flush=True)
    for r in eligible[:10]:
        print(f"  #{r['rank']:>2} hold PF {r['hold_pf']:.2f} "
              f"({r['hold_trades']} trades, {r['hold_assets']} assets, "
              f"${r['hold_pnl_usd']:.0f}) — {r['label']}", flush=True)
    print(f"[league] done in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
