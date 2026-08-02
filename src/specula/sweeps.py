"""Config grids for sweeps — shared by sweep scripts and walk-forward."""

import itertools
import json

FEES = [0.0004, 0.001]          # crypto: futures taker / spot taker
EQUITY_FEES = [0.0001, 0.0005]  # equities: zero-commission, cost is spread (1bp / 5bp)

TF_PAIRS = [
    ("4h", e) for e in ["1h", "30min", "15min", "5min", "1min"]
] + [
    ("2h", e) for e in ["30min", "15min", "5min", "1min"]
] + [
    ("1h", e) for e in ["30min", "15min", "5min", "1min"]
] + [
    ("30min", e) for e in ["15min", "5min", "1min"]
] + [
    ("15min", e) for e in ["5min", "1min"]
] + [
    ("5min", "1min"),
]


def pair_configs(setup_tf: str, exec_tf: str, symbol: str = "BTCUSDT",
                 fees: list[float] | None = None):
    fees = FEES if fees is None else fees
    for dev, strict, target, fee in itertools.product(
        [2.0, 2.5], [True, False], ["r1", "r2", "midband", "opposite"], fees
    ):
        yield dict(
            strategy="fffd", symbol=symbol, setup_tf=setup_tf,
            exec_tf=exec_tf, dev=dev, strict=strict, target=target, fee=fee,
        )
    for adx, sl, tp, fee in itertools.product(
        [True, False], [0.005, 0.01], [0.005, 0.01], fees
    ):
        yield dict(
            strategy="didi", symbol=symbol, setup_tf=setup_tf,
            exec_tf=exec_tf, tol_bars=1, adx_filter=adx, sl=sl, tp=tp, fee=fee,
        )


def cfg_label(cfg: dict, with_fee: bool = False) -> str:
    """Compact human label for a config (mirrors the web app's setupLabel)."""
    strategy = cfg.get("strategy")
    if strategy == "didi":
        bits = [f"Didi {cfg.get('setup_tf')}→{cfg.get('exec_tf')}"]
        if cfg.get("adx_filter"):
            bits.append("ADX")
        bits += [f"sl {cfg.get('sl', 0) * 100:g}%", f"tp {cfg.get('tp', 0) * 100:g}%"]
    elif strategy == "fffd":
        bits = [
            f"FFFD {cfg.get('setup_tf')}→{cfg.get('exec_tf')}",
            "strict" if cfg.get("strict") else "loose",
            f"dev {cfg.get('dev')}",
            str(cfg.get("target")),
        ]
    elif strategy == "lab":
        entry = cfg.get("entry", {})
        exit_spec = cfg.get("exit", {})
        bits = [f"{entry.get('kind', '?')} {cfg.get('setup_tf')}→{cfg.get('exec_tf')}"]
        bits += [f"{k} {v}" for k, v in entry.items() if k != "kind"]
        ex = f"exit {exit_spec.get('kind', '?')}"
        for k in ("sl", "tp", "max_bars"):
            if exit_spec.get(k) is not None:
                ex += f" {k}={exit_spec[k]}"
        bits.append(ex)
    else:
        bits = [json.dumps({k: v for k, v in cfg.items() if k != "fee"},
                           sort_keys=True)[:70]]
    if with_fee and cfg.get("fee") is not None:
        bits.append(f"fee {cfg['fee'] * 100:.2f}%")
    return " · ".join(bits)
