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

export function setupKey(run) {
  const p = { ...run.params }
  delete p.fee
  const sorted = Object.keys(p)
    .sort()
    .reduce((o, k) => ((o[k] = p[k]), o), {})
  return `${run.symbol}|${run.sweep_tag}|${JSON.stringify(sorted)}`
}

export function setupLabel(run) {
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
