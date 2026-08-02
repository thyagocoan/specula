"""Compact interactive HTML reports for logged runs.

vectorbt's default pf.plot() embeds every bar into the HTML — at 1-minute
execution that is a >100 MB file. This builds a browser-friendly figure
instead: equity vs buy&hold (resampled to <=20k points) plus drawdown,
with the full-resolution stats unaffected (they come from the registry).
"""

from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from specula.sweeps import cfg_label

MAX_POINTS = 20_000


def _thin(series):
    if len(series) > MAX_POINTS:
        rule = "1h" if len(series) > 100_000 else "15min"
        return series.resample(rule).last().dropna()
    return series


def report_figure(pf, cfg: dict, run_id: str) -> go.Figure:
    value = _thin(pf.value())
    bench = _thin(pf.close.dropna())
    bench = bench / bench.iloc[0] * float(pf.init_cash)
    dd = value / value.cummax() - 1

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28],
        vertical_spacing=0.06, subplot_titles=("Equity", "Drawdown"),
    )
    fig.add_trace(go.Scatter(x=value.index, y=value.values, name="strategy",
                             line=dict(color="#2a78d6", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bench.index, y=bench.values, name="buy & hold",
                             line=dict(color="#eb6834", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="drawdown",
                             fill="tozeroy", line=dict(color="#e34948", width=1.5),
                             showlegend=False), row=2, col=1)
    fig.update_yaxes(tickformat=".1%", row=2, col=1)
    fig.update_layout(
        title=f"{cfg_label(cfg, with_fee=True)} | run {run_id}",
        template="plotly_white", height=620,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=60, r=30, t=80, b=40),
    )
    return fig


def save_report(pf, cfg: dict, run_id: str, reports_dir: Path = Path("reports")) -> Path:
    reports_dir.mkdir(exist_ok=True)
    dest = reports_dir / f"{run_id}.html"
    report_figure(pf, cfg, run_id).write_html(dest, include_plotlyjs=True)
    return dest
