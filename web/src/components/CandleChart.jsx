import { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

// TradingView-style candlestick chart with trade markers. `range`
// ({from, to} unix seconds) zooms the view to that window; null fits all.
export default function CandleChart({ candles, markers, height = 420, range = null }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !candles?.length) return
    const css = getComputedStyle(document.documentElement)
    const v = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback
    const chart = createChart(ref.current, {
      height,
      layout: {
        background: { color: v('--surface', '#fcfcfb') },
        textColor: v('--ink-2', '#52514e'),
      },
      grid: {
        vertLines: { color: v('--grid', '#e1e0d9') },
        horzLines: { color: v('--grid', '#e1e0d9') },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    })
    const series = chart.addCandlestickSeries({
      upColor: '#1baf7a', downColor: '#e34948',
      wickUpColor: '#1baf7a', wickDownColor: '#e34948',
      borderVisible: false,
    })
    series.setData(candles)
    if (markers?.length) {
      series.setMarkers([...markers].sort((a, b) => a.time - b.time))
    }
    if (range) {
      try {
        chart.timeScale().setVisibleRange(range)
      } catch {
        chart.timeScale().fitContent()
      }
    } else {
      chart.timeScale().fitContent()
    }
    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [candles, markers, height, range])

  return <div ref={ref} style={{ width: '100%' }} />
}
