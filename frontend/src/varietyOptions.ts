import type { EChartsCoreOption } from 'echarts/core'
import type { SavedChart } from './api'

const colors = ['#176c54', '#e0a831', '#4d76aa', '#b95849', '#8b6baa', '#789b69']

function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
}

function tooltip(title: string, lines: [string, unknown][]): string {
  return `<strong>${escapeHtml(title)}</strong>${lines.map(([label, value]) => `<br>${escapeHtml(label)}: ${value == null ? 'Unavailable' : escapeHtml(value)}`).join('')}`
}

export function varietyOption(saved: SavedChart): EChartsCoreOption | null {
  const { chart } = saved
  if (chart.chart_type === 'donut') {
    const rings = chart.series.length
    return {
      color: colors,
      legend: { type: 'scroll', bottom: 0, textStyle: { color: '#41544b' } },
      tooltip: { trigger: 'item', formatter: (item: unknown) => {
        const entry = item as { seriesName: string; name: string; value: number; percent: number }
        return tooltip(`${entry.seriesName}: ${entry.name}`, [['Dismissals', entry.value], ['Share', `${entry.percent}%`]])
      } },
      series: chart.series.map((series, index) => ({
        name: series.name,
        type: 'pie',
        radius: rings === 1 ? ['38%', '67%'] : index === 0 ? ['22%', '42%'] : ['49%', '69%'],
        center: ['50%', '46%'],
        label: { show: true, formatter: `{b}: {c}`, color: '#315743' },
        labelLine: { length: 10, length2: 8 },
        data: series.points.filter(point => typeof point.y === 'number').map(point => ({ name: String(point.x), value: point.y, sample_size: point.sample_size })),
      })),
    } as EChartsCoreOption
  }
  if (chart.chart_type === 'heatmap') {
    const points = chart.series.flatMap(series => series.points)
    const bowlers = [...new Set(points.map(point => String(point.x)))]
    const phases = ['Powerplay', 'Middle', 'Death'].filter(phase => points.some(point => point.row === phase))
    const values = points.map(point => point.y).filter((value): value is number => typeof value === 'number')
    const max = Math.max(100, ...values)
    return {
      animationDuration: 350,
      grid: { top: 28, left: 90, right: 30, bottom: bowlers.length > 6 ? 125 : 100, containLabel: true },
      tooltip: { trigger: 'item', formatter: (item: unknown) => {
        const entry = item as { data: { bowler: string; phase: string; value: [number, number, number]; runs: number; sample_size: number } }
        return tooltip(`${entry.data.bowler} · ${entry.data.phase}`, [['Runs per 100 legal balls', entry.data.value[2].toFixed(1)], ['Batter runs', entry.data.runs], ['Legal balls', entry.data.sample_size]])
      } },
      xAxis: { type: 'category', data: bowlers, name: chart.x_label, nameLocation: 'middle', nameGap: 75, axisLabel: { rotate: bowlers.length > 5 ? 35 : 0, hideOverlap: true, color: '#5d6b62' } },
      yAxis: { type: 'category', data: phases, name: chart.y_label, nameLocation: 'middle', nameGap: 68, axisLabel: { color: '#5d6b62' } },
      visualMap: { min: 0, max, calculable: true, orient: 'horizontal', left: 'center', bottom: 0, inRange: { color: ['#edf5e9', '#7eb8a0', '#176c54'] }, textStyle: { color: '#41544b' } },
      series: [{ type: 'heatmap', data: points.filter(point => typeof point.y === 'number' && phases.includes(String(point.row))).map(point => ({ value: [bowlers.indexOf(String(point.x)), phases.indexOf(String(point.row)), point.y], bowler: String(point.x), phase: String(point.row), runs: point.runs, sample_size: point.sample_size })), label: { show: true, formatter: (item: { value: [number, number, number] }) => item.value[2].toFixed(0), color: '#183b2d' }, emphasis: { itemStyle: { borderColor: '#193b2e', borderWidth: 2 } } }],
    } as EChartsCoreOption
  }
  if (chart.chart_type === 'bubble') {
    const points = chart.series.flatMap(series => series.points)
    const sizes = points.map(point => Number(point.size)).filter(Number.isFinite)
    const maxSize = Math.max(1, ...sizes)
    return {
      animationDuration: 350,
      color: colors,
      grid: { top: 35, left: 70, right: 30, bottom: 70, containLabel: true },
      tooltip: { trigger: 'item', formatter: (item: unknown) => {
        const entry = item as { data: { name: string; value: [number, number, number]; sample_size: number; runs: number; dismissals: number } }
        return tooltip(entry.data.name, [['Batting average', entry.data.value[0].toFixed(1)], ['Runs per 100 legal balls', entry.data.value[1].toFixed(1)], ['Legal balls', entry.data.value[2]], ['Innings', entry.data.sample_size], ['Runs', entry.data.runs], ['Dismissals', entry.data.dismissals]])
      } },
      xAxis: { type: 'value', name: chart.x_label, nameLocation: 'middle', nameGap: 35, min: 0, splitLine: { lineStyle: { color: '#eaf0e9' } } },
      yAxis: { type: 'value', name: chart.y_label, nameLocation: 'middle', nameGap: 55, min: 0, splitLine: { lineStyle: { color: '#eaf0e9' } } },
      series: [{ type: 'scatter', symbolSize: (value: number[]) => 12 + 34 * Math.sqrt(Math.max(0, value[2]) / maxSize), data: points.filter(point => typeof point.x === 'number' && typeof point.y === 'number' && typeof point.size === 'number').map(point => ({ name: String(point.player_name ?? point.name ?? point.player_id ?? ''), value: [point.x, point.y, point.size], sample_size: point.sample_size, runs: point.runs, dismissals: point.dismissals })), label: { show: false, position: 'top', formatter: '{b}', color: '#315743', fontSize: 10 }, emphasis: { label: { show: true } }, itemStyle: { color: colors[0], opacity: .75 } }],
    } as EChartsCoreOption
  }
  if (chart.chart_type === 'stacked_area') {
    const innings = [...new Set(chart.series.map(series => series.innings_number).filter((value): value is number => typeof value === 'number'))].sort((a, b) => a - b)
    if (!innings.length) return null
    const components = ['running', 'boundary', 'other_batter', 'extras']
    const axes = innings.map(number => [...new Set(chart.series.filter(series => series.innings_number === number).flatMap(series => series.points.map(point => Number(point.x))))].sort((a, b) => a - b))
    return {
      animationDuration: 350,
      color: colors,
      legend: { type: 'scroll', bottom: 0, textStyle: { color: '#41544b' } },
      tooltip: { trigger: 'axis', formatter: (items: unknown) => (items as { axisValue: string; seriesName: string; value: number }[]).map(item => tooltip(`${item.seriesName} · over ${item.axisValue}`, [['Runs', item.value]])).join('<br><br>') },
      grid: innings.map((_, index) => ({ left: 62, right: 24, top: `${7 + index * (82 / innings.length)}%`, height: `${68 / innings.length}%`, containLabel: true })),
      xAxis: innings.map((_, index) => ({ type: 'category', gridIndex: index, data: axes[index].map(String), name: chart.x_label, nameLocation: 'middle', nameGap: 25, axisLabel: { hideOverlap: true, color: '#5d6b62' } })),
      yAxis: innings.map((_, index) => ({ type: 'value', gridIndex: index, name: chart.y_label, min: 0, nameLocation: 'middle', nameGap: 40, splitLine: { lineStyle: { color: '#eaf0e9' } } })),
      series: chart.series.map(series => {
        const panel = innings.indexOf(series.innings_number ?? -1)
        const color = colors[components.indexOf(String((series as { component?: string }).component))] ?? colors[0]
        return { name: series.name, type: 'line', stack: `innings-${series.innings_number}`, xAxisIndex: panel, yAxisIndex: panel, areaStyle: { opacity: .78 }, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color }, data: axes[panel].map(x => series.points.find(point => Number(point.x) === x)?.y ?? null) }
      }),
    } as EChartsCoreOption
  }
  if (chart.chart_type === 'stacked_bar') {
    const categories = [...new Set(chart.series.flatMap(series => series.points.map(point => String(point.x))))]
    return {
      animationDuration: 350,
      color: colors,
      grid: { top: 42, left: 65, right: 24, bottom: categories.length > 8 ? 100 : 72, containLabel: true },
      legend: { top: 0, textStyle: { color: '#41544b' } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (items: unknown) => (items as { seriesName: string; axisValue: string; value: number; data?: { batter_name?: string } }[]).map(item => tooltip(`${item.axisValue} · ${item.seriesName}`, [['Batter', item.data?.batter_name], ['Runs', item.value]])).join('<br><br>') },
      xAxis: { type: 'category', data: categories, name: chart.x_label, nameLocation: 'middle', nameGap: 55, axisLabel: { rotate: categories.length > 6 ? 30 : 0, hideOverlap: true, color: '#5d6b62' } },
      yAxis: { type: 'value', name: chart.y_label, nameLocation: 'middle', nameGap: 45, min: 0, splitLine: { lineStyle: { color: '#eaf0e9' } } },
      dataZoom: categories.length > 12 ? [{ type: 'inside' }, { type: 'slider', bottom: 5, height: 15 }] : [],
      series: chart.series.map(series => ({ name: series.name, type: 'bar', stack: 'partnership', barMaxWidth: 44, data: categories.map(x => { const point = series.points.find(item => String(item.x) === x); return { value: point?.y ?? null, batter_name: point?.batter_name } }) })),
    } as EChartsCoreOption
  }
  return null
}
