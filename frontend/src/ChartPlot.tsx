import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, HeatmapChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, VisualMapComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import type { SavedChart } from './api'
import { varietyOption } from './varietyOptions'

echarts.use([BarChart, LineChart, ScatterChart, PieChart, HeatmapChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, VisualMapComponent, SVGRenderer])

const colors = ['#176c54', '#e0a831', '#4d76aa', '#b95849']

function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
}

export function chartOption(saved: SavedChart): EChartsCoreOption {
  const variety = varietyOption(saved)
  if (variety) return variety
  const { chart } = saved
  const numericScatter = chart.chart_type === 'scatter' && chart.group_by === 'balls_faced'
  const timeAxis = chart.group_by === 'innings_date'
  const matchView = chart.group_by === 'over' && (chart.metric === 'runs_per_over' || chart.metric === 'cumulative_runs')
  const categories = [...new Set(chart.series.flatMap(series => series.points.map(point => String(point.x))))]
  if (['year', 'over', 'rolling_innings'].includes(chart.group_by)) categories.sort((a, b) => Number(a) - Number(b))
  const categoryPoint = (point: (typeof chart.series)[number]['points'][number] | undefined) => ({
    value: point?.y ?? null,
    sample_size: point?.sample_size,
    wickets: point?.wickets,
    ...(matchView && point?.wickets ? {
      itemStyle: chart.chart_type === 'line' ? { color: '#b44945' } : undefined,
      symbolSize: chart.chart_type === 'line' ? 12 : undefined,
      label: { show: true, position: 'top', formatter: point.wickets > 1 ? `● ${point.wickets}` : '●', color: '#b44945', fontSize: 14, fontWeight: 'bold', distance: 4 },
    } : {}),
  })
  const bySeries = chart.series.map((series, index) => ({
    name: series.name,
    type: chart.chart_type,
    data: numericScatter || timeAxis
      ? series.points.map(point => ({ value: [numericScatter ? Number(point.x) : String(point.x), point.y], sample_size: point.sample_size }))
      : categories.map(x => categoryPoint(series.points.find(item => String(item.x) === x))),
    itemStyle: { color: colors[index % colors.length] },
    lineStyle: { width: series.name.toLowerCase().includes('baseline') ? 2 : 3, type: series.name.toLowerCase().includes('baseline') ? 'dashed' : 'solid' },
    symbolSize: series.name.toLowerCase().includes('baseline') ? 0 : chart.chart_type === 'scatter' ? 10 : 7,
    barMaxWidth: 44,
    connectNulls: false,
  }))
  return {
    animationDuration: 350,
    color: colors,
    grid: { top: matchView ? 56 : 36, left: 58, right: 24, bottom: categories.length > 12 ? 76 : 56, containLabel: true },
    legend: { show: chart.series.length > 1, top: 0, textStyle: { color: '#41544b' } },
    tooltip: {
      trigger: chart.chart_type === 'scatter' ? 'item' : 'axis',
      backgroundColor: '#fff', borderColor: '#dae5dc', textStyle: { color: '#193b2e' },
      formatter: (params: unknown) => {
        const items = Array.isArray(params) ? params : [params]
        return items.map(item => {
          const entry = item as { axisValue?: unknown; name?: unknown; seriesName?: unknown; value?: number | [number, number | null] | null; data?: { sample_size?: number; wickets?: number } }
          const x = (numericScatter || timeAxis) && Array.isArray(entry.value) ? entry.value[0] : entry.axisValue ?? entry.name
          const y = Array.isArray(entry.value) ? entry.value[1] : entry.value
          const sample = entry.data?.sample_size
          const wickets = matchView && typeof entry.data?.wickets === 'number' ? `<br>Wickets: ${escapeHtml(entry.data.wickets)}` : ''
          return `<strong>${escapeHtml(entry.seriesName)}</strong><br>${escapeHtml(chart.x_label)}: ${escapeHtml(x)}<br>${escapeHtml(chart.y_label)}: ${y == null ? 'Unavailable' : escapeHtml(y)}${wickets}${sample == null ? '' : `<br>Sample: ${escapeHtml(sample)}`}`
        }).join('<br><br>')
      },
    },
    xAxis: numericScatter ? { type: 'value', name: chart.x_label, nameLocation: 'middle', nameGap: 30, min: 0 } : timeAxis ? { type: 'time', name: chart.x_label, nameLocation: 'middle', nameGap: 30, axisLabel: { color: '#5d6b62' } } : { type: 'category', name: chart.x_label, nameLocation: 'middle', nameGap: 30, data: categories, axisLabel: { interval: 0, hideOverlap: true, color: '#5d6b62' } },
    yAxis: { type: 'value', name: chart.y_label, nameLocation: 'middle', nameGap: 45, min: 0, axisLabel: { color: '#5d6b62' }, splitLine: { lineStyle: { color: '#eaf0e9' } } },
    dataZoom: (numericScatter || timeAxis || categories.length > 12) ? [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 15 }] : [],
    series: bySeries,
  } as EChartsCoreOption
}

export default function ChartPlot({ saved, onSvgReady }: { saved: SavedChart; onSvgReady: (getSvg: (() => string) | null) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const option = useMemo(() => chartOption(saved), [saved])
  useEffect(() => {
    if (!host.current) return
    const instance = echarts.init(host.current, undefined, { renderer: 'svg' })
    instance.setOption(option)
    onSvgReady(() => instance.getDataURL({ type: 'svg', pixelRatio: 2, backgroundColor: '#ffffff' }))
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(host.current)
    return () => { observer.disconnect(); onSvgReady(null); instance.dispose() }
  }, [option, onSvgReady])
  return <div ref={host} className={`plot${saved.chart.chart_type === 'stacked_area' ? ' plot--tall' : ''}`} />
}
