import { useMemo, useRef, useState } from 'react'

// Drawdown area chart: negative fractions filled down from a 0 baseline.
export default function Area({ points, height = 130, color = '#e34948' }) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null)
  const width = 860
  const pad = { l: 52, r: 16, t: 8, b: 22 }

  const { tMin, tMax, vMin } = useMemo(() => {
    const ts = points.map((p) => Date.parse(p.t))
    const lo = Math.min(...points.map((p) => p.v), -0.01)
    return { tMin: Math.min(...ts), tMax: Math.max(...ts), vMin: lo * 1.1 }
  }, [points])

  const xs = (t) => pad.l + ((t - tMin) / Math.max(tMax - tMin, 1)) * (width - pad.l - pad.r)
  const ys = (v) => pad.t + (v / vMin) * (height - pad.t - pad.b)

  const poly = useMemo(() => {
    const line = points.map((p) => `${xs(Date.parse(p.t))},${ys(p.v)}`)
    return [`${xs(tMin)},${ys(0)}`, ...line, `${xs(tMax)},${ys(0)}`].join(' ')
  }, [points, tMin, tMax, vMin, height])

  const yticks = [0, vMin / 2, vMin]

  function onMove(e) {
    const box = wrapRef.current.getBoundingClientRect()
    const svg = wrapRef.current.querySelector('svg').getBoundingClientRect()
    const mx = ((e.clientX - svg.left) / svg.width) * width
    const t = tMin + ((mx - pad.l) / (width - pad.l - pad.r)) * (tMax - tMin)
    let best = null
    for (const p of points) {
      if (best == null || Math.abs(Date.parse(p.t) - t) < Math.abs(Date.parse(best.t) - t)) best = p
    }
    if (best) {
      setHover({ ...best, x: xs(Date.parse(best.t)),
        left: e.clientX - box.left + 12, top: e.clientY - box.top + 10 })
    }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', display: 'block' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
        aria-label="Drawdown over time">
        {yticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={width - pad.r} y1={ys(t)} y2={ys(t)}
              stroke={t === 0 ? 'var(--baseline)' : 'var(--grid)'} />
            <text x={pad.l - 8} y={ys(t) + 4} textAnchor="end" fontSize="11"
              fill="var(--muted)">{(t * 100).toFixed(1)}%</text>
          </g>
        ))}
        <polygon points={poly} fill={color} fillOpacity="0.28" stroke={color}
          strokeWidth="1.5" strokeLinejoin="round" />
        {hover && (
          <line x1={hover.x} x2={hover.x} y1={pad.t} y2={height - pad.b}
            stroke="var(--baseline)" strokeWidth="1" />
        )}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: hover.left, top: hover.top }}>
          <div className="t-title">{hover.t}</div>
          <div className="t-row"><span>drawdown</span><b>{(hover.v * 100).toFixed(2)}%</b></div>
        </div>
      )}
    </div>
  )
}
