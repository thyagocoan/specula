import { useMemo, useRef, useState } from 'react'
import { num } from '../data.js'

// Multi-series time line chart with crosshair hover.
// series: [{ name, color, points: [{t: 'YYYY-MM-DD', v: number}] }]
export default function Line({ series, height = 300, yLabel = '' }) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null)
  const width = 860
  const pad = { l: 52, r: 16, t: 14, b: 34 }

  const { tMin, tMax, vMin, vMax } = useMemo(() => {
    const ts = series.flatMap((s) => s.points.map((p) => Date.parse(p.t)))
    const vs = series.flatMap((s) => s.points.map((p) => p.v))
    const lo = Math.min(...vs, 1)
    const hi = Math.max(...vs, 1)
    const m = (hi - lo) * 0.08 || 0.02
    return { tMin: Math.min(...ts), tMax: Math.max(...ts), vMin: lo - m, vMax: hi + m }
  }, [series])

  const xs = (t) => pad.l + ((t - tMin) / Math.max(tMax - tMin, 1)) * (width - pad.l - pad.r)
  const ys = (v) => pad.t + (1 - (v - vMin) / (vMax - vMin)) * (height - pad.t - pad.b)

  const yticks = useMemo(() => {
    const step = (vMax - vMin) / 4
    return [0, 1, 2, 3, 4].map((i) => vMin + i * step)
  }, [vMin, vMax])

  const xticks = useMemo(() => {
    const n = 5
    return [...Array(n)].map((_, i) => tMin + ((tMax - tMin) * i) / (n - 1))
  }, [tMin, tMax])

  function onMove(e) {
    const box = wrapRef.current.getBoundingClientRect()
    const svg = wrapRef.current.querySelector('svg').getBoundingClientRect()
    const mx = ((e.clientX - svg.left) / svg.width) * width
    const t = tMin + ((mx - pad.l) / (width - pad.l - pad.r)) * (tMax - tMin)
    const vals = series.map((s) => {
      let best = null
      for (const p of s.points) {
        const pt = Date.parse(p.t)
        if (best == null || Math.abs(pt - t) < Math.abs(Date.parse(best.t) - t)) best = p
      }
      return { name: s.name, color: s.color, point: best }
    }).filter((v) => v.point)
    if (!vals.length) return setHover(null)
    setHover({
      x: xs(Date.parse(vals[0].point.t)),
      vals,
      left: e.clientX - box.left + 14,
      top: e.clientY - box.top + 10,
    })
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <div style={{ marginBottom: 8 }}>
        {series.map((s) => (
          <span key={s.name} className="chip">
            <span className="dot" style={{ background: s.color }} />{s.name}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', display: 'block' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
        aria-label={`${yLabel} over time`}>
        {yticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={width - pad.r} y1={ys(t)} y2={ys(t)} stroke="var(--grid)" />
            <text x={pad.l - 8} y={ys(t) + 4} textAnchor="end" fontSize="11"
              fill="var(--muted)">{num(t)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={width - pad.r} y1={ys(1)} y2={ys(1)}
          stroke="var(--baseline)" strokeWidth="1.5" strokeDasharray="5 4" />
        {xticks.map((t, i) => (
          <text key={i} x={xs(t)} y={height - 8} textAnchor="middle" fontSize="11"
            fill="var(--muted)">{new Date(t).toISOString().slice(0, 10)}</text>
        ))}
        {series.map((s) => (
          <polyline key={s.name} fill="none" stroke={s.color} strokeWidth="2"
            strokeLinejoin="round" strokeLinecap="round"
            points={s.points.map((p) => `${xs(Date.parse(p.t))},${ys(p.v)}`).join(' ')} />
        ))}
        {hover && (
          <line x1={hover.x} x2={hover.x} y1={pad.t} y2={height - pad.b}
            stroke="var(--baseline)" strokeWidth="1" />
        )}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: hover.left, top: hover.top }}>
          <div className="t-title">{hover.vals[0].point.t}</div>
          {hover.vals.map((v) => (
            <div key={v.name} className="t-row">
              <span><span className="dot" style={{
                background: v.color, display: 'inline-block',
                width: 8, height: 8, borderRadius: 4, marginRight: 6,
              }} />{v.name}</span>
              <b>{num(v.point.v, 3)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
