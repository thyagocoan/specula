import { useMemo, useState } from 'react'
import Scatter from '../components/Scatter.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { groupSetups, num, setupLabel } from '../data.js'

export default function Overview({ runs, generatedAt }) {
  const [minTrades, setMinTrades] = useState(20)
  const setups = useMemo(() => groupSetups(runs), [runs])
  const symbols = useMemo(() => new Set(runs.map((r) => r.symbol)), [runs])

  const viable = runs.filter((r) => (r.n_trades ?? 0) >= 30 && r.profit_factor != null)
  const best = [...viable].sort((a, b) => b.profit_factor - a.profit_factor)[0]

  const points = useMemo(
    () =>
      runs
        .filter((r) => (r.n_trades ?? 0) >= minTrades && r.profit_factor != null)
        .map((r) => ({
          run_id: r.run_id,
          x: r.n_trades,
          y: r.profit_factor,
          ret: r.total_return_pct,
          fee: r.params.fee,
          strategy: r.strategy,
          label: setupLabel(r),
        })),
    [runs, minTrades],
  )

  const top = useMemo(
    () =>
      groupSetups(runs.filter((r) => (r.n_trades ?? 0) >= 30))
        .filter((s) => s.pf_low != null)
        .sort((a, b) => b.pf_low - a.pf_low)
        .slice(0, 8),
    [runs],
  )

  return (
    <div>
      <h1 className="page-title">Overview</h1>
      <p className="page-sub">Registry snapshot · {generatedAt || 'live'}</p>
      <div className="tiles">
        <div className="tile"><div className="k">Backtests logged</div><div className="v">{runs.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Distinct setups</div><div className="v">{setups.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Assets</div><div className="v">{symbols.size}</div></div>
        <div className="tile">
          <div className="k">Best profit factor (≥30 trades)</div>
          <div className="v">{best ? num(best.profit_factor) : '—'}</div>
          <div className="d">{best ? setupLabel(best) : 'no qualifying run yet'}</div>
        </div>
      </div>

      <div className="card">
        <h3>Profit factor vs. trade count — every logged run</h3>
        <div className="filters">
          <label>min trades
            <input type="number" min="1" value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value) || 1)} />
          </label>
          <span className="hint">{points.length} runs shown · dots above the dashed line are net winners after costs</span>
        </div>
        <Scatter points={points} />
      </div>

      <div className="card">
        <h3>Top setups (by profit factor at the lowest fee, ≥30 trades)</h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th className="txt">Asset</th><th>Trades</th>
              <th>Win %</th><th>PF @0.04%</th><th>PF @0.10%</th><th>Return</th><th>Max DD</th><th>Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {top.map((s) => (
              <tr key={s.key}>
                <td className="txt">{s.label}</td>
                <td className="txt">{s.symbol}</td>
                <td>{s.n_trades}</td>
                <td>{num(s.win_rate, 1)}</td>
                <td><Pf v={s.pf_low} /></td>
                <td><Pf v={s.pf_high} /></td>
                <td><Ret v={s.total_return} /></td>
                <td>{num(s.max_dd, 1)}%</td>
                <td>{num(s.sharpe)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
