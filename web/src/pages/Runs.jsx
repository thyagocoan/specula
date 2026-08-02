import { useMemo, useState } from 'react'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { num, setupLabel, uniqueSorted } from '../data.js'

export default function Runs({ runs }) {
  const [symbol, setSymbol] = useState('all')
  const [strategy, setStrategy] = useState('all')
  const [sweep, setSweep] = useState('all')
  const [minTrades, setMinTrades] = useState(0)
  const [sort, setSort] = useState({ col: 'profit_factor', dir: 'desc' })
  const [selected, setSelected] = useState(null)

  const symbols = uniqueSorted(runs.map((r) => r.symbol))
  const sweeps = uniqueSorted(runs.map((r) => r.sweep_tag))

  const rows = useMemo(
    () =>
      sortRows(
        runs.filter(
          (r) =>
            (symbol === 'all' || r.symbol === symbol) &&
            (strategy === 'all' || r.strategy === strategy) &&
            (sweep === 'all' || r.sweep_tag === sweep) &&
            (r.n_trades ?? 0) >= minTrades,
        ),
        sort.col,
        sort.dir,
      ),
    [runs, symbol, strategy, sweep, minTrades, sort],
  )

  return (
    <div>
      <h1 className="page-title">Runs</h1>
      <p className="page-sub">Every backtest ever executed — click a row for its full parameters.</p>
      <div className="card">
        <div className="filters">
          <label>asset
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="all">All</option>
              {symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="all">All</option>
              <option value="didi">Didi</option>
              <option value="fffd">FFFD</option>
            </select>
          </label>
          <label>sweep
            <select value={sweep} onChange={(e) => setSweep(e.target.value)}>
              <option value="all">All</option>
              {sweeps.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>min trades
            <input type="number" min="0" value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value) || 0)} />
          </label>
          <span className="hint">{rows.length.toLocaleString()} runs</span>
        </div>
        <table className="grid">
          <thead>
            <tr>
              <Th id="run_id" sort={sort} setSort={setSort} txt>Run</Th>
              <Th id="sweep_tag" sort={sort} setSort={setSort} txt>Sweep</Th>
              <th className="txt">Setup</th>
              <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
              <Th id="win_rate_pct" sort={sort} setSort={setSort}>Win %</Th>
              <Th id="profit_factor" sort={sort} setSort={setSort}>PF</Th>
              <Th id="avg_trade_pct" sort={sort} setSort={setSort}>Avg trade</Th>
              <Th id="total_return_pct" sort={sort} setSort={setSort}>Return</Th>
              <Th id="max_dd_pct" sort={sort} setSort={setSort}>Max DD</Th>
              <Th id="sharpe" sort={sort} setSort={setSort}>Sharpe</Th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 300).map((r) => (
              <tr key={r.run_id} className="selectable" onClick={() => setSelected(r)}>
                <td className="txt" style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }}>{r.run_id}</td>
                <td className="txt">{r.sweep_tag}</td>
                <td className="txt">{setupLabel(r)} · fee {(r.params.fee * 100).toFixed(2)}%</td>
                <td>{r.n_trades}</td>
                <td>{num(r.win_rate_pct, 1)}</td>
                <td><Pf v={r.profit_factor} /></td>
                <td>{num(r.avg_trade_pct, 3)}%</td>
                <td><Ret v={r.total_return_pct} /></td>
                <td>{num(r.max_dd_pct, 1)}%</td>
                <td>{num(r.sharpe)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > 300 && <p className="hint">showing first 300 — tighten the filters</p>}
      </div>

      {selected && (
        <div className="detail">
          <button className="btn ghost close" onClick={() => setSelected(null)}>✕</button>
          <h3>Run {selected.run_id}</h3>
          <p className="hint">
            {selected.created_at} · commit {selected.git_sha} · {selected.sweep_tag}
          </p>
          {selected.report ? (
            <p><a href={`/reports/${selected.run_id}.html`} target="_blank" rel="noreferrer">
              open interactive chart report
            </a></p>
          ) : (
            <p className="hint">
              No chart generated yet — run:<br />
              <code>uv run python scripts/plot_run.py {selected.run_id}</code>
            </p>
          )}
          <h3>Parameters</h3>
          <pre>{JSON.stringify(selected.params, null, 2)}</pre>
          <h3>Metrics</h3>
          <pre>{JSON.stringify(
            {
              n_trades: selected.n_trades,
              win_rate_pct: selected.win_rate_pct,
              profit_factor: selected.profit_factor,
              avg_trade_pct: selected.avg_trade_pct,
              total_return_pct: selected.total_return_pct,
              max_dd_pct: selected.max_dd_pct,
              sharpe: selected.sharpe,
            },
            null,
            2,
          )}</pre>
        </div>
      )}
    </div>
  )
}
