"""Setup League — one-shot round: favourites + auto candidates on ALL assets.

Results MERGE into data/meta/setup_league.json (the League page store);
first_seen dates and post-discovery stats survive across rounds. For the
endless explore-until-paused mode, see scripts/league_explorer.py.

Run: uv run python scripts/setup_league.py [--holdout-days 60]
     [--max-configs 40] [--symbols CSX,LLY] [--workers 8]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def candidate_configs(max_configs: int, min_assets: int) -> list[dict]:
    from specula import runlog
    from specula.league import label_of, sig_of

    out: dict[str, dict] = {}
    for f in runlog.fav_setups_list():
        if f["params"]:
            sig = sig_of(f["params"])
            out[sig] = {
                "sig": sig,
                "label": f["label"] or label_of(f["params"]),
                "params": {k: v for k, v in f["params"].items()
                           if k not in ("symbol", "fee")},
                "source": "favourite",
            }
    n_favs = len(out)

    df = runlog.load()
    df = df[df["profit_factor"].notna() & (df["n_trades"] >= 30)]
    stats: dict[str, dict] = {}
    for r in df.to_dict("records"):
        params = json.loads(r["params"])
        sig = sig_of(params)
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
        params = {k: v for k, v in st["params"].items()
                  if k not in ("symbol", "fee")}
        out[sig] = {"sig": sig, "label": label_of(st["params"]),
                    "params": params, "source": f"auto (median PF {med:.2f})"}
    print(f"[league] candidates: {n_favs} favourites + "
          f"{len(out) - n_favs} auto = {len(out)}", flush=True)
    return list(out.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-days", type=int, default=60)
    ap.add_argument("--max-configs", type=int, default=40)
    ap.add_argument("--min-assets", type=int, default=8)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    from specula import league, runlog

    configs = candidate_configs(args.max_configs, args.min_assets)
    if not configs:
        print("[league] no candidates — star some setups first", flush=True)
        return
    symbols = None  # evaluate() defaults to the stocks-only universe
    if args.symbols:
        keep = {x.strip().upper() for x in args.symbols.split(",")}
        symbols = [x for x in league.universe() if x in keep]

    keep_sigs = {league.sig_of(f["params"])
                 for f in runlog.fav_setups_list() if f["params"]}
    rows, meta = league.evaluate(
        configs, holdout_days=args.holdout_days, symbols=symbols,
        workers=args.workers, first_seen_map=league.first_seen_map(),
        registry_keep_sigs=keep_sigs)
    if not rows:
        print("[league] no trades at all", flush=True)
        return
    league.merge_store(rows, meta)

    ranked = sorted((r for r in rows if r["eligible"] and r["hold_pf"]),
                    key=lambda r: r["hold_pf"] or 0, reverse=True)
    print(f"[league] wrote {league.STORE} — top of this round:", flush=True)
    for r in ranked[:10]:
        print(f"  hold PF {r['hold_pf']:.2f} ({r['hold_trades']} trades, "
              f"{r['hold_assets']} assets, ${r['hold_pnl_usd']:.0f}) — "
              f"{r['label']}", flush=True)
    print("[league] done", flush=True)


if __name__ == "__main__":
    main()
