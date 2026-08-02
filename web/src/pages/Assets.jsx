import { useEffect, useMemo, useState } from 'react'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { groupSetups, num } from '../data.js'

// returns of the asset's best-setup equity curve over the trailing N days
function curveReturn(points, days) {
  if (!points?.length) return null
  const last = points[points.length - 1]
  const cutoff = Date.parse(last.t) - days * 86400e3
  const window = points.filter((p) => Date.parse(p.t) >= cutoff)
  if (window.length < 2) return null
  return 100 * (last.v / window[0].v - 1)
}

function oosPf(wf, symbol) {
  let best = null
  for (const d of (wf?.symbols || [])) {
    if (d.symbol !== symbol && d.symbol !== `${symbol}·lab`) continue
    const pf = d.scenarios?.[0]?.aggregate?.oos_pf
    if (pf != null && (best === null || pf > best)) best = pf
  }
  return best
}

export default function Assets({ runs, onReview }) {
  const [wf, setWf] = useState(null)
  const [curves, setCurves] = useState(null)
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) setWf(d)
        }
      } catch { /* none */ }
      try {
        const r = await fetch('/data/curves.json')
        if (r.ok) setCurves(await r.json())
      } catch { /* none */ }
    })()
  }, [])

  const rows = useMemo(() => {
    const grouped = groupSetups(runs.filter((r) => (r.n_trades ?? 0) >= 30))
      .filter((s) => s.pf_low != null)
    const bestByAsset = new Map()
    for (const s of grouped) {
      const prev = bestByAsset.get(s.symbol)
      if (!prev || s.pf_low > prev.pf_low) bestByAsset.set(s.symbol, s)
    }
    return [...bestByAsset.values()].map((s) => {
      const pts = curves?.curves?.[s.symbol]?.points
      return {
        symbol: s.symbol,
        label: s.label,
        pf_low: s.pf_low,
        oos_pf: oosPf(wf, s.symbol),
        ret_day: curveReturn(pts, 1),
        ret_week: curveReturn(pts, 7),
        ret_month: curveReturn(pts, 30),
      }
    })
  }, [runs, wf, curves])

  const sorted = useMemo(() => sortRows(rows, sort.col, sort.dir), [rows, sort])

  return (
    <div>
      <h1 className="page-title">Assets</h1>
      <p className="page-sub">
        One line per asset: its best analyzed strategy, the out-of-sample
        verdict, and that strategy's returns over the trailing day, week and
        month (in-sample curve). Click an asset for the full review.
      </p>
      <div className="card">
        <table className="grid">
          <thead>
            <tr>
              <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
              <th className="txt">Best strategy</th>
              <Th id="pf_low" sort={sort} setSort={setSort}>PF (in-sample)</Th>
              <Th id="oos_pf" sort={sort} setSort={setSort}>PF (OOS)</Th>
              <Th id="ret_day" sort={sort} setSort={setSort}>Day</Th>
              <Th id="ret_week" sort={sort} setSort={setSort}>Week</Th>
              <Th id="ret_month" sort={sort} setSort={setSort}>Month</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.symbol} className="selectable"
                onClick={() => onReview?.(r.symbol)}>
                <td className="txt"><b>{r.symbol}</b></td>
                <td className="txt">{r.label}</td>
                <td>{num(r.pf_low)}</td>
                <td><Pf v={r.oos_pf} /></td>
                <td>{r.ret_day != null ? <Ret v={r.ret_day} /> : '—'}</td>
                <td>{r.ret_week != null ? <Ret v={r.ret_week} /> : '—'}</td>
                <td>{r.ret_month != null ? <Ret v={r.ret_month} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">{rows.length} assets · returns are relative to the
          latest data day · PF (OOS) is the walk-forward verdict — trust it
          over the in-sample column</p>
      </div>
    </div>
  )
}
