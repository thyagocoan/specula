"""Config grids for sweeps — shared by sweep scripts and walk-forward."""

import itertools

FEES = [0.0004, 0.001]

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


def pair_configs(setup_tf: str, exec_tf: str, symbol: str = "BTCUSDT"):
    for dev, strict, target, fee in itertools.product(
        [2.0, 2.5], [True, False], ["r1", "r2", "midband", "opposite"], FEES
    ):
        yield dict(
            strategy="fffd", symbol=symbol, setup_tf=setup_tf,
            exec_tf=exec_tf, dev=dev, strict=strict, target=target, fee=fee,
        )
    for adx, sl, tp, fee in itertools.product(
        [True, False], [0.005, 0.01], [0.005, 0.01], FEES
    ):
        yield dict(
            strategy="didi", symbol=symbol, setup_tf=setup_tf,
            exec_tf=exec_tf, tol_bars=1, adx_filter=adx, sl=sl, tp=tp, fee=fee,
        )


def cfg_label(cfg: dict, with_fee: bool = False) -> str:
    """Compact human label for a config (mirrors the web app's setupLabel)."""
    if cfg["strategy"] == "didi":
        bits = [f"Didi {cfg['setup_tf']}→{cfg['exec_tf']}"]
        if cfg.get("adx_filter"):
            bits.append("ADX")
        bits += [f"sl {cfg['sl'] * 100:g}%", f"tp {cfg['tp'] * 100:g}%"]
    else:
        bits = [
            f"FFFD {cfg['setup_tf']}→{cfg['exec_tf']}",
            "strict" if cfg.get("strict") else "loose",
            f"dev {cfg['dev']}",
            str(cfg["target"]),
        ]
    if with_fee:
        bits.append(f"fee {cfg['fee'] * 100:.2f}%")
    return " · ".join(bits)
