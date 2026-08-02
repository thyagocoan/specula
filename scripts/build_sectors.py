"""One-time (re-runnable) build of the symbol→sector map.

Sources: Wikipedia's S&P 500 constituent table (GICS sectors) for stocks;
everything ending in USDT/USDC is Crypto; anything unmatched is Other.
Writes data/meta/sectors.json used by the portal and risk views.
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OUT = Path("data/meta/sectors.json")
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def main() -> None:
    import pandas as pd

    from specula.data import equity_symbols

    import io

    req = urllib.request.Request(URL, headers={"User-Agent": "specula/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    table = next(t for t in tables if "Symbol" in t.columns
                 and "GICS Sector" in t.columns)
    wiki = {str(r["Symbol"]).replace(".", "-"): str(r["GICS Sector"])
            for _, r in table.iterrows()}

    out = {}
    missing = []
    for sym in sorted(equity_symbols()):
        # lake symbols use '.' (BRK.B); wiki uses '-' (BRK-B)
        sector = wiki.get(sym) or wiki.get(sym.replace(".", "-"))
        if sector:
            out[sym] = sector
        else:
            out[sym] = "Other"
            missing.append(sym)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=0, sort_keys=True),
                   encoding="utf-8")
    sectors = sorted(set(out.values()))
    print(f"wrote {OUT}: {len(out)} stocks, {len(sectors)} sectors "
          f"({', '.join(sectors)})")
    if missing:
        print(f"unmatched -> Other: {', '.join(missing)}")


if __name__ == "__main__":
    main()
