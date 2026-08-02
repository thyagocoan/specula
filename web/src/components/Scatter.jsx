import { useMemo, useRef, useState } from 'react'
import { num } from '../data.js'

const COLORS = { didi: 'var(--series-1)', fffd: 'var(--series-2)' }
const NAMES = { didi: 'Didi', fffd: 'FFFD' }

// Profit factor vs number of trades, one dot per run, colored by strategy.
export default function Scatter({ points, height = 360 }) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null)
  const width = 860
  const pad = { l: 46, r: 16, t: 14, b: 40 }
  const yMax = 3

  const xMax = useMemo(
    () => Math.max(100, ...points.map((p) => p.x)),
    [points],
  )
  const xs = (v) =>
    pad.l + (Math.log10(Math.max(v, 1)) / Math.log10(xMax)) * (width - pad.l - pad.r)
  const ys = (v) =>
    pad.t + (1 - Math.min(v, yMax) / yMax) * (height - pad.t - pad.b)

  const xticks = [1, 3, 10, 30, 100, 300, 1000].filter((t) => t <= xMax * 1.05)
  const yticks = [0, 0.5, 1, 1.5, 2, 2.5, 3]

  const positioned = useMemo(
    () => points.map((p) => ({ ...p, px: xs(p.x), py: ys(p.y) })),
    [points, xMax, height],
  )

  function onMove(e) {
    const box = wrapRef.current.getBoundingClientRect()
    const svg = wrapRef.current.querySelector('svg').getBoundingClientRect()
    const mx = ((e.clientX - svg.left) / svg.width) * width
    const my = ((e.clientY - svg.top) / svg.height) * height
    let best = null
    let bestD = 14 * 14
    for (const p of positioned) {
      const d = (p.px - mx) ** 2 + (p.py - my) ** 2
      if (d < bestD) { bestD = d; best = p }
    }
    setHover(
      best && {
        ...best,
        left: e.clientX - box.left + 14,
        top: e.clientY - box.top + 14,
      },
    )
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <div style={{ marginBottom: 8 }}>
        {Object.keys(NAMES).map((k) => (
          <span key={k} className="chip">
            <span className="dot" style={{ background: COLORS[k] }} />
            {NAMES[k]}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="Profit factor versus number of trades, by strategy"
      >
        {yticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={ys(t)} y2={ys(t)}
              stroke={t === 1 ? 'var(--baseline)' : 'var(--grid)'}
              strokeWidth={t === 1 ? 1.5 : 1}
              strokeDasharray={t === 1 ? '5 4' : undefined} />
            <text x={pad.l - 8} y={ys(t) + 4} textAnchor="end" fontSize="11"
              fill="var(--muted)">{t}</text>
          </g>
        ))}
        <text x={pad.l - 8} y={ys(1) - 6} textAnchor="end" fontSize="10"
          fill="var(--muted)">break-even</text>
        {xticks.map((t) => (
          <g key={t}>
            <line x1={xs(t)} x2={xs(t)} y1={height - pad.b} y2={height - pad.b + 4}
              stroke="var(--baseline)" />
            <text x={xs(t)} y={height - pad.b + 18} textAnchor="middle" fontSize="11"
              fill="var(--muted)">{t}</text>
          </g>
        ))}
        <line x1={pad.l} x2={width - pad.r} y1={height - pad.b} y2={height - pad.b}
          stroke="var(--baseline)" />
        <text x={(pad.l + width - pad.r) / 2} y={height - 4} textAnchor="middle"
          fontSize="11" fill="var(--muted)">trades (log scale)</text>
        <text x={12} y={pad.t + 10} fontSize="11" fill="var(--muted)"
          transform={`rotate(-90 12 ${pad.t + 10})`} textAnchor="end">profit factor</text>
        {positioned.map((p) => (
          <circle key={p.run_id} cx={p.px} cy={p.py} r={hover?.run_id === p.run_id ? 6 : 4}
            fill={COLORS[p.strategy]} fillOpacity="0.75"
            stroke={hover?.run_id === p.run_id ? 'var(--surface)' : 'none'} strokeWidth="2" />
        ))}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: hover.left, top: hover.top }}>
          <div className="t-title">{hover.label}</div>
          <div className="t-row"><span>Profit factor</span><b>{num(hover.y)}</b></div>
          <div className="t-row"><span>Trades</span><b>{hover.x}</b></div>
          <div className="t-row"><span>Return</span><b>{num(hover.ret, 1)}%</b></div>
          <div className="t-row"><span>Fee/side</span><b>{(hover.fee * 100).toFixed(2)}%</b></div>
        </div>
      )}
    </div>
  )
}
