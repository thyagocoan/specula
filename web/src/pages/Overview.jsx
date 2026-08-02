import { useEffect, useMemo, useRef, useState } from 'react'
import CandleChart from '../components/CandleChart.jsx'
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

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

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

const CHART_TF = { 1: '5min', 7: '30min', 30: '2h', all: '1d' }

const fmtTime = (iso, tz) => new Date(iso).toLocaleString('en-AU', {
  timeZone: tz, day: '2-digit', month: 'short',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

function SetupCurve({ runId, days, symbol, onClose }) {
  const [trades, setTrades] = useState(null)
  const [candles, setCandles] = useState(null)
  const [pl, setPl] = useState(null)
  const [err, setErr] = useState(null)
  const crypto = /USD[TC]$/.test(symbol)
  const marketTz = crypto ? 'UTC' : 'America/New_York'
  const tf = CHART_TF[days ?? 'all']

  useEffect(() => {
    setTrades(null); setCandles(null); setPl(null); setErr(null)
    ;(async () => {
      try {
        const [tr, cr, pr] = await Promise.all([
          fetch(`/api/trades/${runId}`),
          fetch(`/api/candles/${symbol}?tf=${tf}${days ? `&days=${days}` : ''}`),
          fetch(`/api/curve/${runId}`),
        ])
        if (!tr.ok || !cr.ok) throw new Error('failed to load trade data')
        setTrades(await tr.json())
        setCandles(await cr.json())
        if (pr.ok) setPl(await pr.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [runId, days, symbol, tf])

  const windowTrades = useMemo(() => {
    if (!trades || !candles?.length) return null
    const startSec = candles[0].time
    return trades.filter((t) => Date.parse(t.entry_ts) / 1000 >= startSec)
  }, [trades, candles])

  const markers = useMemo(() => {
    if (!windowTrades) return []
    const m = []
    for (const t of windowTrades) {
      const long = t.side === 'long'
      const ret = t.net_return_pct ?? t.return_pct
      m.push({
        time: Math.floor(Date.parse(t.entry_ts) / 1000),
        position: long ? 'belowBar' : 'aboveBar',
        color: long ? '#1baf7a' : '#e34948',
        shape: long ? 'arrowUp' : 'arrowDown',
        text: long ? 'L' : 'S',
      })
      if (t.exit_ts) {
        m.push({
          time: Math.floor(Date.parse(t.exit_ts) / 1000),
          position: long ? 'aboveBar' : 'belowBar',
          color: (ret ?? 0) >= 0 ? '#1baf7a' : '#e34948',
          shape: 'circle',
          text: `${ret > 0 ? '+' : ''}${ret?.toFixed(2)}%`,
        })
      }
    }
    return m
  }, [windowTrades])

  // compound the stake trade by trade: each trade reinvests the running
  // balance, so the next trade uses the result of the previous P&L
  const ledger = useMemo(() => {
    if (!windowTrades?.length) return null
    const start = windowTrades[0].size_usd ?? 1000
    let equity = start
    const rows = windowTrades.map((t) => {
      const stake = equity
      const pnl = t.net_return_pct != null ? stake * t.net_return_pct / 100 : null
      if (pnl != null) equity += pnl
      return { ...t, stake, pnl, equity }
    })
    return { rows, start, final: equity }
  }, [windowTrades])

  return (
    <div className="card">
      <h3>
        {symbol} — triggers on the chart ({tf} candles, chart times UTC)
        <button className="btn ghost" style={{ float: 'right' }}
          onClick={onClose}>close</button>
      </h3>
      {pl && <p className="hint" style={{ margin: '0 0 6px' }}>{pl.label}</p>}
      {err && <p className="hint">{err}</p>}
      {!candles && !err && <p className="hint">loading chart + trades…</p>}
      {candles && (
        <>
          <div style={{ marginBottom: 6 }}>
            <span className="chip">
              triggers in period: <b style={{ marginLeft: 4 }}>{windowTrades?.length ?? 0}</b>
            </span>
            {ledger && (
              <span className="chip">net P&L in period:{' '}
                <Money v={ledger.final - ledger.start} />{' '}
                <span className="hint">on ${num(ledger.start, 0)} start,
                  compounded, {crypto ? 'Binance' : 'IBKR'} fees</span>
              </span>
            )}
          </div>
          <CandleChart candles={candles} markers={markers} />
          <h3 style={{ marginTop: 14 }}>Trigger log{' '}
            <span className="hint">(cross-check in TradingView — market time is
              {crypto ? ' UTC' : ' New York'} · P&L is net of{' '}
              {crypto ? 'Binance' : 'IBKR'} fees · each trade stakes the
              running balance)</span></h3>
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Entry (Melbourne)</th>
                <th className="txt">Entry ({crypto ? 'UTC' : 'New York'})</th>
                <th className="txt">Side</th><th>Entry px</th>
                <th className="txt">Exit ({crypto ? 'UTC' : 'New York'})</th>
                <th>Exit px</th><th>Stake $</th><th>P&L %</th>
                <th>P&L $</th><th>Balance $</th>
              </tr>
            </thead>
            <tbody>
              {(ledger?.rows || []).slice().reverse().slice(0, 100).map((t, i) => (
                <tr key={i}>
                  <td className="txt">{fmtTime(t.entry_ts, 'Australia/Melbourne')}</td>
                  <td className="txt">{fmtTime(t.entry_ts, marketTz)}</td>
                  <td className="txt">{t.side}</td>
                  <td>{t.entry_price}</td>
                  <td className="txt">{t.exit_ts ? fmtTime(t.exit_ts, marketTz) : 'open'}</td>
                  <td>{t.exit_price ?? '—'}</td>
                  <td>{num(t.stake, 2)}</td>
                  <td>{t.net_return_pct != null ? <Ret v={t.net_return_pct} /> : '—'}</td>
                  <td><Money v={t.pnl} /></td>
                  <td>{num(t.equity, 2)}</td>
                </tr>
              ))}
            </tbody>
            {ledger && (
              <tfoot>
                <tr>
                  <td className="txt" colSpan={6}>
                    <b>Total — {ledger.rows.length} trades, started
                      ${num(ledger.start, 0)}</b>
                  </td>
                  <td />
                  <td>
                    <Ret v={100 * (ledger.final / ledger.start - 1)} />
                  </td>
                  <td><Money v={ledger.final - ledger.start} /></td>
                  <td><b>{num(ledger.final, 2)}</b></td>
                </tr>
              </tfoot>
            )}
          </table>
        </>
      )}
    </div>
  )
}

function AssetReview({ symbol, runs, wf, days }) {
  const [selectedRun, setSelectedRun] = useState(null)
  const [runTrades, setRunTrades] = useState({})
  const fetched = useRef(new Set())
  const setups = useMemo(
    () => groupSetups(runs.filter((r) => r.symbol === symbol))
      .filter((s) => s.pf_low != null)
      .sort((a, b) => b.pf_low - a.pf_low)
      .slice(0, 15),
    [runs, symbol],
  )
  const wfDocs = (wf?.symbols || []).filter(
    (d) => d.symbol === symbol || d.symbol === `${symbol}·lab`)

  // the setup curves/journal treat as this asset's best (top PF, ≥30 trades)
  const favKey = useMemo(
    () => (setups.find((s) => s.n_trades >= 30) ?? setups[0])?.key,
    [setups],
  )

  // pull each setup's real trades (few at a time; server caches them) so the
  // table can show activity for whatever time window is selected
  useEffect(() => {
    let alive = true
    const ids = setups.map((s) => s.byFee?.[s.fees?.[0]]?.run_id)
      .filter((id) => id && !fetched.current.has(id))
    ids.forEach((id) => fetched.current.add(id))
    const queue = [...ids]
    const workers = Array.from({ length: 3 }, async () => {
      while (queue.length && alive) {
        const id = queue.shift()
        try {
          const r = await fetch(`/api/trades/${id}`)
          if (r.ok) {
            const t = await r.json()
            if (alive) setRunTrades((prev) => ({ ...prev, [id]: t }))
          } else {
            fetched.current.delete(id)
          }
        } catch {
          fetched.current.delete(id)
        }
      }
    })
    Promise.all(workers)
    return () => { alive = false }
  }, [setups])

  // "last week" etc. is anchored to the newest trade of this asset, so the
  // window matches the data's last day, not the wall clock
  const refT = useMemo(() => {
    let m = null
    for (const arr of Object.values(runTrades)) {
      for (const t of arr) {
        const x = Date.parse(t.entry_ts)
        if (!m || x > m) m = x
      }
    }
    return m
  }, [runTrades])

  const periodLabel = PERIODS.find(([, , d]) => d === days)?.[1] ?? 'All time'

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

      {selectedRun && (
        <SetupCurve runId={selectedRun} days={days} symbol={symbol}
          onClose={() => setSelectedRun(null)} />
      )}

      <div className="card">
        <h3>Top strategies for {symbol}{' '}
          <span className="hint">(★ = best, used by curves &amp; journal ·
            click a row for its chart, trigger log and period activity)</span></h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th>Trades</th><th>Win %</th>
              <th>PF @low fee</th><th>PF @high fee</th><th>Return</th>
              <th>Max DD</th><th>Sharpe</th>
              <th>Trades ({periodLabel})</th><th>P&L $ ({periodLabel})</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((s) => {
              const runId = s.byFee?.[s.fees?.[0]]?.run_id
              const tr = runTrades[runId]
              let pn = null, pPnl = null
              if (tr) {
                const win = (days != null && refT)
                  ? tr.filter((t) => Date.parse(t.entry_ts) >= refT - days * 86400e3)
                  : tr
                pn = win.length
                pPnl = win.reduce((a, t) => a + (t.pnl_usd || 0), 0)
              }
              return (
                <tr key={s.key} className={runId ? 'selectable' : ''}
                  onClick={() => runId && setSelectedRun(runId)}>
                  <td className="txt">
                    {s.key === favKey && (
                      <span title="favourite — this asset's best setup"
                        style={{ color: '#e8b93c', marginRight: 6 }}>★</span>
                    )}
                    {s.label}
                  </td>
                  <td>{s.n_trades}</td>
                  <td>{num(s.win_rate, 1)}</td>
                  <td><Pf v={s.pf_low} /></td>
                  <td><Pf v={s.pf_high} /></td>
                  <td><Ret v={s.total_return} /></td>
                  <td>{num(s.max_dd, 1)}%</td>
                  <td>{num(s.sharpe, 2)}</td>
                  <td>{tr ? pn : <span className="hint">…</span>}</td>
                  <td>{tr ? <Money v={pPnl} /> : <span className="hint">…</span>}</td>
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

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) setWf(d)
        }
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
          <AssetReview symbol={asset} runs={tabRuns} wf={wf} days={days} />
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
