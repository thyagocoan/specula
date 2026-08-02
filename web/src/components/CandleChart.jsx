import { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

// TradingView-style candlestick chart with trade markers. `range`
// ({from, to} unix seconds) zooms the view to that window; null fits all.
// `lines`: [{name, color, points:[{time, value}]}] overlaid on the price
// scale. `rsi`: [{time, value}] drawn as an oscillator in the bottom band.
export default function CandleChart({
  candles, markers, height = 420, range = null, lines = [], rsi = null,
}) {
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
    const hasRsi = rsi?.length > 0
    series.priceScale().applyOptions({
      scaleMargins: { top: 0.06, bottom: hasRsi ? 0.26 : 0.06 },
    })
    series.setData(candles)
    for (const l of lines) {
      const ls = chart.addLineSeries({
        color: l.color, lineWidth: 1.5, title: l.name,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      ls.setData(l.points)
    }
    if (hasRsi) {
      const rs = chart.addLineSeries({
        color: '#8a68c9', lineWidth: 1, title: 'RSI 14',
        priceScaleId: 'rsi', priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      })
      chart.priceScale('rsi').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0.02 },
      })
      rs.setData(rsi)
      rs.createPriceLine({ price: 70, color: v('--grid', '#e1e0d9'), lineStyle: 3, axisLabelVisible: false })
      rs.createPriceLine({ price: 30, color: v('--grid', '#e1e0d9'), lineStyle: 3, axisLabelVisible: false })
    }
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
  }, [candles, markers, height, range, lines, rsi])

  return <div ref={ref} style={{ width: '100%' }} />
}
