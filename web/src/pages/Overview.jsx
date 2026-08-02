import { useEffect, useMemo, useState } from 'react'
import Area from '../components/Area.jsx'
import Line from '../components/Line.jsx'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { groupSetups, isCrypto, num } from '../data.js'

const TABS = [
  ['all', 'All'],
  ['crypto', 'Crypto'],
  ['stocks', 'Stocks'],
]

const PERIODS = [
  ['all', 'All time', null],
  ['30', 'Month', 30],
  ['7', 'Week', 7],
  ['1', 'Day', 1],
]

function slicedRebased(points, days) {
  if (!points?.length) return null
  let pts = points
  if (days != null) {
    const maxT = Date.parse(points[points.length - 1].t)
    const cutoff = maxT - days * 86400e3
    pts = points.filter((p) => Date.parse(p.t) >= cutoff)
  }
  if (pts.length < 2) return null
  const base = pts[0].v
  return pts.map((p) => ({ t: p.t, v: p.v / base }))
}

function drawdown(points) {
  let peak = -Infinity
  return points.map((p) => {
    peak = Math.max(peak, p.v)
    return { t: p.t, v: p.v / peak - 1 }
  })
}

// out-of-sample trades + P&L for one symbol within the selected period,
// from the walk-forward equity curve (one point per stitched OOS trade)
function wfGlobalMaxT(wf) {
  let m = null
  for (const d of wf?.symbols || []) {
    for (const s of d.scenarios || []) {
      const e = s.equity
      if (e?.length) {
        const t = Date.parse(e[e.length - 1].t)
        if (!m || t > m) m = t
      }
    }
  }
  return m
}

function wfPeriod(wf, symbol, days) {
  const docs = (wf?.symbols || []).filter(
    (d) => d.symbol === symbol || d.symbol === `${symbol}·lab`)
  let best = null
  for (const d of docs) {
    const s = d.scenarios?.[0]
    if (s?.equity?.length && (!best || s.equity.length > best.equity.length)) {
      best = s
    }
  }
  if (!best) return { trades: null, pnl: null }
  let pts = best.equity
  if (days != null) {
    // window relative to the latest data day across ALL assets, so "last
    // day" means the same calendar window for every symbol
    const refT = wfGlobalMaxT(wf) ?? Date.parse(pts[pts.length - 1].t)
    pts = pts.filter((p) => Date.parse(p.t) >= refT - days * 86400e3)
  }
  if (!pts.length) return { trades: 0, pnl: 0 }
  if (pts.length === 1) return { trades: 1, pnl: 0 }
  return {
    trades: pts.length,
    pnl: 100 * (pts[pts.length - 1].v / pts[0].v - 1),
  }
}

function ReportPanel({ symbol, curves, days }) {
  const c = curves?.curves?.[symbol]
  const strat = useMemo(() => slicedRebased(c?.points, days), [c, days])
  const bench = useMemo(() => slicedRebased(c?.bench, days), [c, days])
  const dd = useMemo(() => (strat ? drawdown(strat) : null), [strat])

  if (!strat) {
    return (
      <div className="card">
        <h3>{symbol} — best setup vs buy &amp; hold</h3>
        <p className="hint">no curve data for this asset/period yet</p>
      </div>
    )
  }
  return (
    <div className="card">
      <h3>{symbol} — best setup vs buy &amp; hold</h3>
      {c && <p className="hint" style={{ margin: '0 0 6px' }}>{c.label} · in-sample</p>}
      <div style={{ marginBottom: 6 }}>
        <span className="chip">
          <span className="dot" style={{ background: 'var(--series-1)' }} />
          strategy: <Ret v={100 * (strat[strat.length - 1].v - 1)} />
        </span>
        {bench && (
          <span className="chip">
            <span className="dot" style={{ background: 'var(--series-2)' }} />
            buy &amp; hold: <Ret v={100 * (bench[bench.length - 1].v - 1)} />
          </span>
        )}
        <span className="chip">
          max DD: <b style={{ marginLeft: 4 }}>
            {(100 * Math.min(...dd.map((p) => p.v))).toFixed(1)}%</b>
        </span>
      </div>
      <Line yLabel="equity multiple" height={240} series={[
        { name: 'strategy', color: 'var(--series-1)', points: strat },
        ...(bench ? [{ name: 'buy & hold', color: 'var(--series-2)', points: bench }] : []),
      ]} />
      <Area points={dd} height={120} />
    </div>
  )
}

function SetupCurve({ runId, days, onClose }) {
  const [doc, setDoc] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setDoc(null)
    setErr(null)
    ;(async () => {
      try {
        const r = await fetch(`/api/curve/${runId}`)
        if (!r.ok) throw new Error(`curve build failed (${r.status})`)
        setDoc(await r.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [runId])

  const strat = useMemo(() => slicedRebased(doc?.points, days), [doc, days])
  const price = useMemo(() => slicedRebased(doc?.price, days), [doc, days])

  return (
    <div className="card">
      <h3>
        Setup P&L vs price
        <button className="btn ghost" style={{ float: 'right' }}
          onClick={onClose}>close</button>
      </h3>
      {err && <p className="hint">{err}</p>}
      {!doc && !err && <p className="hint">building curve…</p>}
      {doc && (
        <>
          <p className="hint" style={{ margin: '0 0 6px' }}>{doc.label}</p>
          {!strat ? (
            <p className="hint">no activity in the selected period</p>
          ) : (
            <>
              <div style={{ marginBottom: 6 }}>
                <span className="chip">
                  <span className="dot" style={{ background: 'var(--series-1)' }} />
                  setup P&L: <Ret v={100 * (strat[strat.length - 1].v - 1)} />
                </span>
                {price && (
                  <span className="chip">
                    <span className="dot" style={{ background: 'var(--series-2)' }} />
                    price move: <Ret v={100 * (price[price.length - 1].v - 1)} />
                  </span>
                )}
              </div>
              <Line yLabel="relative to period start" height={260} series={[
                { name: 'setup P&L', color: 'var(--series-1)', points: strat },
                ...(price ? [{ name: 'price', color: 'var(--series-2)', points: price }] : []),
              ]} />
            </>
          )}
        </>
      )}
    </div>
  )
}

function AssetReview({ symbol, runs, wf, curves, days }) {
  const [selectedRun, setSelectedRun] = useState(null)
  const setups = useMemo(
    () => groupSetups(runs.filter((r) => r.symbol === symbol))
      .filter((s) => s.pf_low != null)
      .sort((a, b) => b.pf_low - a.pf_low)
      .slice(0, 15),
    [runs, symbol],
  )
  const wfDocs = (wf?.symbols || []).filter(
    (d) => d.symbol === symbol || d.symbol === `${symbol}·lab`)
  const period = wfPeriod(wf, symbol, days)

  return (
    <>
      {wfDocs.length > 0 && (
        <div className="card">
          <h3>Out-of-sample verdict (walk-forward) — the numbers that count</h3>
          <table className="grid">
            <thead>
              <tr><th className="txt">Source</th><th>Fee/side</th><th>OOS trades</th>
                <th>OOS PF</th><th>Win %</th><th>OOS return</th></tr>
            </thead>
            <tbody>
              {wfDocs.flatMap((d) => d.scenarios.map((s) => (
                <tr key={d.symbol + s.fee}>
                  <td className="txt">{d.symbol.endsWith('·lab') ? 'lab strategies' : 'core grid'}</td>
                  <td>{(s.fee * 100).toFixed(2)}%</td>
                  <td>{s.aggregate.oos_trades}</td>
                  <td><Pf v={s.aggregate.oos_pf} /></td>
                  <td>{num(s.aggregate.oos_win_rate_pct, 1)}</td>
                  <td><Ret v={s.aggregate.oos_return_pct} /></td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      )}

      {period.trades != null && (
        <div className="card">
          <h3>Activity in the selected period (out-of-sample)</h3>
          <p style={{ margin: 0 }}>
            <b>{period.trades}</b> daytrades ·
            P&L <Ret v={period.pnl} />
          </p>
        </div>
      )}

      <ReportPanel symbol={symbol} curves={curves} days={days} />

      {selectedRun && (
        <SetupCurve runId={selectedRun} days={days}
          onClose={() => setSelectedRun(null)} />
      )}

      <div className="card">
        <h3>Top strategies for {symbol}{' '}
          <span className="hint">(in-sample · click a row for its P&L-vs-price chart)</span></h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th>Trades</th><th>Win %</th>
              <th>PF @low fee</th><th>PF @high fee</th><th>Return</th><th>Max DD</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((s) => {
              const runId = s.byFee?.[s.fees?.[0]]?.run_id
              return (
                <tr key={s.key} className={runId ? 'selectable' : ''}
                  onClick={() => runId && setSelectedRun(runId)}>
                  <td className="txt">{s.label}</td>
                  <td>{s.n_trades}</td>
                  <td>{num(s.win_rate, 1)}</td>
                  <td><Pf v={s.pf_low} /></td>
                  <td><Pf v={s.pf_high} /></td>
                  <td><Ret v={s.total_return} /></td>
                  <td>{num(s.max_dd, 1)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function Overview({ runs: allRuns, generatedAt, asset, setAsset }) {
  const [tab, setTab] = useState('all')
  const [period, setPeriod] = useState('all')
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })
  const [wf, setWf] = useState(null)
  const [curves, setCurves] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) setWf(d)
        }
      } catch { /* none yet */ }
      try {
        const r = await fetch('/data/curves.json')
        if (r.ok) setCurves(await r.json())
      } catch { /* none yet */ }
    })()
  }, [])

  const days = PERIODS.find(([id]) => id === period)?.[2] ?? null

  const tabRuns = useMemo(
    () => (tab === 'all' ? allRuns
      : allRuns.filter((r) => isCrypto(r.symbol) === (tab === 'crypto'))),
    [allRuns, tab],
  )
  const tabSymbols = useMemo(
    () => [...new Set(tabRuns.map((r) => r.symbol))].sort(),
    [tabRuns],
  )

  const rows = useMemo(() => {
    const grouped = groupSetups(tabRuns.filter((r) => (r.n_trades ?? 0) >= 30))
      .filter((s) => s.pf_low != null)
    // one row per asset: its best setup, plus period OOS stats
    const bestByAsset = new Map()
    for (const s of grouped) {
      const prev = bestByAsset.get(s.symbol)
      if (!prev || s.pf_low > prev.pf_low) bestByAsset.set(s.symbol, s)
    }
    return [...bestByAsset.values()].map((s) => {
      const p = wfPeriod(wf, s.symbol, days)
      return { ...s, period_trades: p.trades, period_pnl: p.pnl }
    })
  }, [tabRuns, wf, days])

  // with a time window selected, show only assets that actually triggered
  const visible = useMemo(
    () => (days == null ? rows : rows.filter((r) => (r.period_trades || 0) > 0)),
    [rows, days],
  )
  const sorted = useMemo(() => sortRows(visible, sort.col, sort.dir),
    [visible, sort])
  const totTrades = visible.reduce((a, r) => a + (r.period_trades || 0), 0)
  const periodLabel = PERIODS.find(([id]) => id === period)?.[1]

  return (
    <div>
      <h1 className="page-title">Overview</h1>
      <p className="page-sub">Registry snapshot · {generatedAt || 'live'}</p>
      <div className="controls">
        <div className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''}
              onClick={() => { setTab(id); setAsset('all') }}>
              {label}
            </button>
          ))}
        </div>
        <div className="select-pill">
          <select value={asset} onChange={(e) => setAsset(e.target.value)}
            aria-label="asset">
            <option value="all">All assets ({tabSymbols.length})</option>
            {tabSymbols.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div className="tabs">
          {PERIODS.map(([id, label]) => (
            <button key={id} className={period === id ? 'active' : ''}
              onClick={() => {
                setPeriod(id)
                if (id !== 'all') setSort({ col: 'period_pnl', dir: 'desc' })
              }}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {asset !== 'all' ? (
        <>
          <p style={{ margin: '0 0 14px' }}>
            <button className="btn ghost" onClick={() => setAsset('all')}>
              ← all assets
            </button>
          </p>
          <AssetReview symbol={asset} runs={tabRuns} wf={wf} curves={curves}
            days={days} />
        </>
      ) : (
        <div className="card">
          <h3>Top setups — best per asset</h3>
          <p className="hint" style={{ marginTop: 0 }}>
            {days == null
              ? `${visible.length} assets with qualifying setups (≥30 trades)`
              : `${visible.length} assets triggered a setup in the last
                 ${periodLabel.toLowerCase()} (of ${rows.length} tracked)`} ·
            out-of-sample daytrades: <b>{totTrades.toLocaleString()}</b> ·
            click a symbol for the full review
          </p>
          <table className="grid">
            <thead>
              <tr>
                <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
                <th className="txt">Best setup (in-sample)</th>
                <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
                <Th id="win_rate" sort={sort} setSort={setSort}>Win %</Th>
                <Th id="pf_low" sort={sort} setSort={setSort}>PF @low fee</Th>
                <Th id="pf_high" sort={sort} setSort={setSort}>PF @high fee</Th>
                <Th id="total_return" sort={sort} setSort={setSort}>Return</Th>
                <Th id="period_trades" sort={sort} setSort={setSort}>
                  OOS trades ({periodLabel})</Th>
                <Th id="period_pnl" sort={sort} setSort={setSort}>
                  OOS P&L ({periodLabel})</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 300).map((s) => (
                <tr key={s.symbol} className="selectable"
                  onClick={() => setAsset(s.symbol)}>
                  <td className="txt"><a href="#" onClick={(e) => {
                    e.preventDefault(); setAsset(s.symbol)
                  }}><b>{s.symbol}</b></a></td>
                  <td className="txt">{s.label}</td>
                  <td>{s.n_trades}</td>
                  <td>{num(s.win_rate, 1)}</td>
                  <td><Pf v={s.pf_low} /></td>
                  <td><Pf v={s.pf_high} /></td>
                  <td><Ret v={s.total_return} /></td>
                  <td>{s.period_trades ?? '—'}</td>
                  <td>{s.period_pnl != null ? <Ret v={s.period_pnl} /> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
