import { useEffect, useState } from 'react'

// favourite strategies, shared by every page. Server-backed (fav_setups
// table — the Setup League reads them there); localStorage is the offline
// fallback and gets migrated up on first load.
export function useFavStrategies() {
  const [favs, setFavs] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('specula-fav-strategies') || '[]')
    } catch {
      return []
    }
  })

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/favsetups')
        if (!r.ok) return
        const server = await r.json()
        if (server.length) {
          setFavs(server.map((f) => ({
            sig: f.sig, label: f.label, status: f.status,
          })))
        } else {
          // one-time migration of purely-local favourites
          let local = []
          try {
            local = JSON.parse(
              localStorage.getItem('specula-fav-strategies') || '[]')
          } catch { /* none */ }
          for (const f of local) {
            fetch('/api/favsetups', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ sig: f.sig, label: f.label }),
            }).catch(() => {})
          }
        }
      } catch { /* offline — localStorage copy stands */ }
    })()
  }, [])

  useEffect(() => {
    localStorage.setItem('specula-fav-strategies', JSON.stringify(favs))
  }, [favs])

  const toggleFav = (sig, label, params) => {
    const removing = favs.some((f) => f.sig === sig)
    setFavs((prev) => removing
      ? prev.filter((f) => f.sig !== sig)
      : [...prev, { sig, label }])
    fetch('/api/favsetups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(removing
        ? { sig, remove: true }
        : { sig, label, params }),
    }).catch(() => {})
  }
  return [favs, toggleFav]
}

// a run's params without asset/fee — what defines the strategy itself
export function strategyParams(run) {
  const p = { ...run.params }
  delete p.fee
  delete p.symbol
  return p
}

export async function loadRuns() {
  const res = await fetch('/data/runs.json')
  if (!res.ok) {
    throw new Error(
      'data/runs.json not found — run: uv run python scripts/export_web_data.py',
    )
  }
  return res.json()
}

export const pct = (x, d = 1) =>
  x == null ? '—' : `${(x * 100).toFixed(d).replace(/\.0+$/, '')}%`

export const num = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d))

// identity of a strategy across assets: params minus symbol and fee
export function strategySig(run) {
  const p = { ...run.params }
  delete p.fee
  delete p.symbol
  const sorted = Object.keys(p)
    .sort()
    .reduce((o, k) => ((o[k] = p[k]), o), {})
  return `${run.strategy}|${JSON.stringify(sorted)}`
}

export function setupKey(run) {
  const p = { ...run.params }
  delete p.fee
  const sorted = Object.keys(p)
    .sort()
    .reduce((o, k) => ((o[k] = p[k]), o), {})
  return `${run.symbol}|${run.sweep_tag}|${JSON.stringify(sorted)}`
}

export function setupLabel(run) {
  const base = setupLabelCore(run)
  const flt = run.params?.filter
  if (!flt) return base
  const extra = Object.entries(flt)
    .filter(([k]) => k !== 'ind')
    .map(([k, v]) => `${k} ${v}`)
    .join(' ')
  return `${base} · gate ${flt.ind}${extra ? ' ' + extra : ''}`
}

function setupLabelCore(run) {
  const p = run.params
  const name = run.strategy === 'didi' ? 'Didi' : 'FFFD'
  if (run.sweep_tag === 'single-tf-v1') {
    return [
      name,
      p.timeframe,
      'v1',
      p.variant || null,
      p.adx_filter ? 'ADX' : null,
      p.sl != null ? `sl ${pct(p.sl)}` : null,
      p.tp != null ? `tp ${pct(p.tp)}` : null,
    ]
      .filter(Boolean)
      .join(' · ')
  }
  if (run.strategy === 'didi') {
    return [
      `Didi ${run.setup_tf}→${run.exec_tf}`,
      p.adx_filter ? 'ADX' : null,
      `sl ${pct(p.sl)}`,
      `tp ${pct(p.tp)}`,
    ]
      .filter(Boolean)
      .join(' · ')
  }
  if (run.strategy === 'lab') {
    const entry = p.entry || {}
    const exit = p.exit || {}
    const NAMES = {
      ma_cross: 'MA cross', orb: 'Opening range', vwap: 'VWAP',
      rsi_cross: 'RSI cross', donchian: 'Donchian', boll: 'Bollinger',
      macd: 'MACD', mom: 'Momentum', fffd_ff: 'FFFD anticipated',
    }
    const bits = [`${NAMES[entry.kind] || entry.kind || '?'} ${run.setup_tf}→${run.exec_tf}`]
    for (const [k, v] of Object.entries(entry)) {
      if (k !== 'kind') bits.push(`${k} ${v}`)
    }
    let ex = `exit ${exit.kind || '?'}`
    if (exit.sl != null) ex += ` sl ${pct(exit.sl)}`
    if (exit.tp != null) ex += ` tp ${pct(exit.tp)}`
    if (exit.max_bars != null) ex += ` ${exit.max_bars} bars`
    bits.push(ex)
    return bits.join(' · ')
  }
  return [
    `FFFD ${run.setup_tf}→${run.exec_tf}`,
    p.strict ? 'strict' : 'loose',
    `dev ${p.dev}`,
    p.target,
  ].join(' · ')
}

// Group runs into "setups": same params except the fee scenario.
export function groupSetups(runs) {
  const map = new Map()
  for (const r of runs) {
    const key = setupKey(r)
    if (!map.has(key)) {
      map.set(key, {
        key,
        label: setupLabel(r),
        symbol: r.symbol,
        strategy: r.strategy,
        setup_tf: r.setup_tf,
        exec_tf: r.exec_tf,
        sweep_tag: r.sweep_tag,
        runs: [],
      })
    }
    map.get(key).runs.push(r)
  }
  for (const s of map.values()) {
    s.byFee = {}
    for (const r of s.runs) s.byFee[r.params.fee] = r
    s.fees = Object.keys(s.byFee)
      .map(Number)
      .sort((a, b) => a - b)
    const low = s.byFee[s.fees[0]]
    s.n_trades = low?.n_trades ?? 0
    const pfs = s.runs.map((r) => r.profit_factor).filter((v) => v != null)
    s.pf_min = pfs.length ? Math.min(...pfs) : null
    s.pf_low = low?.profit_factor ?? null
    s.pf_high = s.byFee[s.fees[s.fees.length - 1]]?.profit_factor ?? null
    s.win_rate = low?.win_rate_pct ?? null
    s.wins = s.win_rate != null
      ? Math.round((s.n_trades * s.win_rate) / 100)
      : null
    s.avg_trade = low?.avg_trade_pct ?? null
    s.total_return = low?.total_return_pct ?? null
    s.max_dd = low?.max_dd_pct ?? null
    s.sharpe = low?.sharpe ?? null
    s.report_run = s.runs.find((r) => r.report)
  }
  return [...map.values()]
}

export function uniqueSorted(arr) {
  return [...new Set(arr)].sort()
}

export const isCrypto = (symbol) => /USD[TC]$/.test(symbol)
