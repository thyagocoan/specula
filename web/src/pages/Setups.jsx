import { useMemo, useState } from 'react'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { groupSetups, num, uniqueSorted } from '../data.js'

export default function Setups({ runs }) {
  const [symbol, setSymbol] = useState('all')
  const [strategy, setStrategy] = useState('all')
  const [sweep, setSweep] = useState('all')
  const [minTrades, setMinTrades] = useState(30)
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })

  const setups = useMemo(() => groupSetups(runs), [runs])
  const symbols = uniqueSorted(runs.map((r) => r.symbol))
  const sweeps = uniqueSorted(runs.map((r) => r.sweep_tag))

  const filtered = useMemo(
    () =>
      sortRows(
        setups.filter(
          (s) =>
            (symbol === 'all' || s.symbol === symbol) &&
            (strategy === 'all' || s.strategy === strategy) &&
            (sweep === 'all' || s.sweep_tag === sweep) &&
            s.n_trades >= minTrades,
        ),
        sort.col,
        sort.dir,
      ),
    [setups, symbol, strategy, sweep, minTrades, sort],
  )

  return (
    <div>
      <h1 className="page-title">Setups</h1>
      <p className="page-sub">
        Runs grouped by identical parameters (fee scenarios shown side by side).
        Metrics are from the lowest-fee run; a setup that stays green in both PF
        columns survives the harsher cost model.
      </p>
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
          <span className="hint">{filtered.length} setups</span>
        </div>
        <table className="grid">
          <thead>
            <tr>
              <Th id="label" sort={sort} setSort={setSort} txt>Setup</Th>
              <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
              <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
              <Th id="win_rate" sort={sort} setSort={setSort}>Win %</Th>
              <Th id="pf_low" sort={sort} setSort={setSort}>PF @0.04%</Th>
              <Th id="pf_high" sort={sort} setSort={setSort}>PF @0.10%</Th>
              <Th id="avg_trade" sort={sort} setSort={setSort}>Avg trade</Th>
              <Th id="total_return" sort={sort} setSort={setSort}>Return</Th>
              <Th id="max_dd" sort={sort} setSort={setSort}>Max DD</Th>
              <Th id="sharpe" sort={sort} setSort={setSort}>Sharpe</Th>
              <th>Report</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map((s) => (
              <tr key={s.key}>
                <td className="txt">{s.label}</td>
                <td className="txt">{s.symbol}</td>
                <td>{s.n_trades}</td>
                <td>{num(s.win_rate, 1)}</td>
                <td><Pf v={s.pf_low} /></td>
                <td><Pf v={s.pf_high} /></td>
                <td>{num(s.avg_trade, 3)}%</td>
                <td><Ret v={s.total_return} /></td>
                <td>{num(s.max_dd, 1)}%</td>
                <td>{num(s.sharpe)}</td>
                <td>
                  {s.report_run
                    ? <a href={`/reports/${s.report_run.run_id}.html`} target="_blank" rel="noreferrer">open</a>
                    : <span className="hint">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 200 && <p className="hint">showing first 200 — tighten the filters</p>}
      </div>
    </div>
  )
}
