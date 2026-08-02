"""Setup League evaluation engine.

Shared by scripts/setup_league.py (one-shot round) and
scripts/league_explorer.py (endless explore-until-paused rounds).

The store (data/meta/setup_league.json) is a merge target keyed by strategy
sig: every evaluated config keeps its first_seen date, and each re-evaluation
refreshes its numbers. Post-discovery metrics (trades entered AFTER the
config was first tested) are the only ranking that endless searching cannot
inflate — new data is virgin by construction.
"""

import hashlib
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

STORE = Path("data/meta/setup_league.json")

DROP_KEYS = {"symbol", "fee"}

# eligibility floors: crypto's universe is ~20 symbols, stocks ~250
MIN_ASSETS = {"all": 5, "stock": 5, "crypto": 3}


def sig_of(params: dict) -> str:
    from specula.sweeps import strategy_sig

    return strategy_sig(params)


def label_of(params: dict) -> str:
    from specula.sweeps import cfg_label

    try:
        return cfg_label(params)
    except Exception:
        return params.get("strategy", "?")


def universe() -> list[str]:
    from specula.data import DATA_ROOT, equity_symbols

    crypto_base = (DATA_ROOT / "bronze" / "crypto" / "exchange=binance"
                   / "market=spot")
    crypto = ({p.name.split("=", 1)[1] for p in crypto_base.glob("symbol=*")}
              if crypto_base.exists() else set())
    return sorted(equity_symbols() | crypto)


def run_symbol(symbol: str, configs: list[dict], fees: dict) -> dict:
    """One worker task: every candidate on one symbol (frames cached once)."""
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
                trades.append((int(idx[int(r["entry_idx"])].value),
                               float(r["return"])))
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


def evaluate(configs: list[dict], holdout_days: int = 60,
             symbols: list[str] | None = None, workers: int | None = None,
             first_seen_map: dict[str, str] | None = None,
             registry_keep_sigs: set[str] | None = None,
             log_registry: bool = True) -> tuple[list[dict], dict]:
    """Run every config on the universe; return (rows, meta).

    Registry rows are logged only for configs worth inspecting per-asset
    (eligible with holdout PF >= 0.95, or in registry_keep_sigs), with
    deterministic ids so re-runs replace instead of duplicating.
    """
    import os

    from specula import runlog
    from specula.data import is_equity
    from specula.settings import get_settings

    s = get_settings()
    fees = {"stock": s["fee_stock_pct"] / 100.0,
            "crypto": s["fee_crypto_pct"] / 100.0}
    sizes = {"stock": s["trade_size_stock_usd"] or 1000.0,
             "crypto": s["trade_size_crypto_usd"] or 100.0}
    first_seen_map = first_seen_map or {}
    registry_keep_sigs = registry_keep_sigs or set()

    syms = symbols or universe()
    workers = workers or max(1, min(8, (os.cpu_count() or 8) - 2))
    print(f"[league] {len(configs)} configs x {len(syms)} assets, "
          f"{workers} workers, holdout {holdout_days}d", flush=True)

    t0 = time.time()
    results: dict[str, dict[str, dict]] = {c["sig"]: {} for c in configs}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_symbol, sym, configs, fees): sym
                for sym in syms}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            done += 1
            try:
                for sig, payload in fut.result().items():
                    if payload is not None:
                        results[sig][sym] = payload
            except Exception as e:
                print(f"[league] {sym} failed: {type(e).__name__}: {e}",
                      flush=True)
            if done % 10 == 0 or done == len(syms):
                print(f"[league] step assets {done}/{len(syms)} "
                      f"({(time.time() - t0) / 60:.1f} min)", flush=True)

    max_ns = max((t[0] for per in results.values() for p in per.values()
                  for t in p["trades"]), default=None)
    if max_ns is None:
        return [], {"cutoff": None, "n_symbols": len(syms)}
    cutoff_ns = max_ns - holdout_days * 86_400 * 10 ** 9

    def aggregate(items, cls: str) -> dict:
        train, hold = [], []
        asset_hold: dict[str, list] = {}
        n_assets = 0
        for sym, payload in items:
            n_assets += 1
            size = sizes["stock"] if is_equity(sym) else sizes["crypto"]
            for ns, ret in payload["trades"]:
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
        stock_items = [(x, p) for x, p in per if is_equity(x)]
        crypto_items = [(x, p) for x, p in per if not is_equity(x)]
        row = {
            "sig": c["sig"], "label": c["label"],
            "source": c.get("source", "?"), "params": c["params"],
            **aggregate(per, "all"),
            "classes": {
                "stock": aggregate(stock_items, "stock"),
                "crypto": aggregate(crypto_items, "crypto"),
            },
        }
        # post-discovery: only trades entered after the config first appeared
        seen = first_seen_map.get(c["sig"])
        if seen:
            seen_ns = int(datetime.fromisoformat(seen).timestamp() * 1e9)
            post = []
            for sym, payload in per:
                size = sizes["stock"] if is_equity(sym) else sizes["crypto"]
                post += [ret * size for ns, ret in payload["trades"]
                         if ns >= seen_ns]
            row["post_trades"] = len(post)
            row["post_pf"] = pooled_pf(post)
            row["post_pnl_usd"] = round(sum(post), 2)
        else:
            row["post_trades"], row["post_pf"], row["post_pnl_usd"] = 0, None, 0.0
        rows.append(row)

    if log_registry:
        keep = {r["sig"] for r in rows
                if (r["eligible"] and (r["hold_pf"] or 0) >= 0.95)} \
            | (registry_keep_sigs & set(results.keys()))
        reg_rows = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sha = runlog.git_sha()
        for sig in keep:
            for sym, payload in results[sig].items():
                cfg = payload["cfg"]
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
        if reg_rows:
            runlog.append(reg_rows)
            print(f"[league] logged {len(reg_rows)} per-asset runs "
                  f"({len(keep)} winner configs) to the registry", flush=True)

    meta = {
        "cutoff": datetime.fromtimestamp(cutoff_ns / 1e9, tz=timezone.utc)
                  .isoformat(timespec="seconds"),
        "n_symbols": len(syms),
        "settings": s,
        "holdout_days": holdout_days,
    }
    return rows, meta


def load_store() -> dict | None:
    if not STORE.exists():
        return None
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return None


def merge_store(rows: list[dict], meta: dict) -> dict:
    """Merge freshly evaluated rows into the store by sig, preserving
    first_seen, then recompute all rankings over the union."""
    def _safe(v):
        if isinstance(v, dict):
            return {k: _safe(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_safe(x) for x in v]
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = load_store() or {}
    merged = {r["sig"]: r for r in old.get("configs", [])}
    for r in rows:
        prev = merged.get(r["sig"])
        r["first_seen"] = prev.get("first_seen", now) if prev else now
        r["rounds_tested"] = (prev.get("rounds_tested", 0) if prev else 0) + 1
        r["last_eval"] = now
        merged[r["sig"]] = r

    all_rows = list(merged.values())

    def rank(get, put):
        eligible = [r for r in all_rows
                    if get(r) and get(r).get("eligible")
                    and get(r).get("hold_pf") is not None
                    and isinstance(get(r)["hold_pf"], (int, float))
                    and math.isfinite(get(r)["hold_pf"])]
        eligible.sort(key=lambda r: get(r)["hold_pf"], reverse=True)
        for r in all_rows:
            put(r, None)
        for i, r in enumerate(eligible):
            put(r, i + 1)

    rank(lambda r: r, lambda r, i: r.update(rank=i))
    for cls in ("stock", "crypto"):
        rank(lambda r, c=cls: r.get("classes", {}).get(c),
             lambda r, i, c=cls: r["classes"][c].update(rank=i))

    doc = {
        "generated_at": now,
        "holdout_days": meta.get("holdout_days"),
        "cutoff": meta.get("cutoff") or old.get("cutoff"),
        "settings": meta.get("settings"),
        "n_symbols": meta.get("n_symbols"),
        "configs": [_safe(r) for r in all_rows],
    }
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def first_seen_map() -> dict[str, str]:
    doc = load_store()
    if not doc:
        return {}
    return {r["sig"]: r["first_seen"] for r in doc.get("configs", [])
            if r.get("first_seen")}
