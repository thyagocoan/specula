import { useMemo, useState } from 'react'
import { Pf, Ret } from '../components/bits.jsx'
import { groupSetups, num, uniqueSorted } from '../data.js'

// The core question of the project: what is the best setup per asset?
export default function Assets({ runs }) {
  const [minTrades, setMinTrades] = useState(30)
  const symbols = uniqueSorted(runs.map((r) => r.symbol))
  const setups = useMemo(() => groupSetups(runs), [runs])

  return (
    <div>
      <h1 className="page-title">Assets</h1>
      <p className="page-sub">
        Best setups per asset, ranked by profit factor at the lowest fee.
        Judge robustness by the pair of PF columns, not one number.
      </p>
      <div className="filters">
        <label>min trades
          <input type="number" min="0" value={minTrades}
            onChange={(e) => setMinTrades(Number(e.target.value) || 0)} />
        </label>
      </div>
      <div className="asset-grid">
        {symbols.map((sym) => {
          const rows = setups
            .filter((s) => s.symbol === sym && s.n_trades >= minTrades && s.pf_low != null)
            .sort((a, b) => b.pf_low - a.pf_low)
            .slice(0, 8)
          const all = runs.filter((r) => r.symbol === sym)
          return (
            <div className="card" key={sym}>
              <h3>{sym} <span className="hint">· {all.length} runs logged</span></h3>
              {rows.length === 0 ? (
                <p className="hint">no setups with ≥{minTrades} trades yet</p>
              ) : (
                <table className="grid">
                  <thead>
                    <tr>
                      <th className="txt">Setup</th><th>Trades</th><th>Win %</th>
                      <th>PF @0.04%</th><th>PF @0.10%</th><th>Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((s) => (
                      <tr key={s.key}>
                        <td className="txt">{s.label}</td>
                        <td>{s.n_trades}</td>
                        <td>{num(s.win_rate, 1)}</td>
                        <td><Pf v={s.pf_low} /></td>
                        <td><Pf v={s.pf_high} /></td>
                        <td><Ret v={s.total_return} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
