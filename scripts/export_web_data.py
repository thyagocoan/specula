"""Export the backtest registry to the web app and sync generated reports.

Usage:
    uv run python scripts/export_web_data.py
"""

import shutil
import sys
from pathlib import Path

from specula import runlog


def main() -> int:
    df = runlog.load()
    runlog.export_web(df)
    print(f"exported {len(df)} runs -> {runlog.WEB_DATA}")

    reports_out = Path("web/public/reports")
    reports_out.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in Path("reports").glob("*.html"):
        dest = reports_out / src.name
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            shutil.copy2(src, dest)
            n += 1
    print(f"synced {n} report file(s) -> {reports_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
