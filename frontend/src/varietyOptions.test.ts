import { expect, it } from 'vitest'
import { chartOption } from './ChartPlot'
import { chartCsv } from './ChartCard'
import type { SavedChart } from './api'

const base: SavedChart = {
  chart_id: 'chart', dataset_id: 'dataset', schema_version: 2,
  chart: { metric: 'dismissals', group_by: 'kind', chart_type: 'donut', title: 'Dismissals', x_label: 'Kind', y_label: 'Dismissals', series: [] },
  coverage: {}, provenance: [],
}

it('renders format-separated dismissal rings from exact counts', () => {
  const saved = { ...base, chart: { ...base.chart, series: [
    { name: 'ODI', points: [{ x: 'caught', y: 6 }, { x: 'bowled', y: 2 }] },
    { name: 'T20I', points: [{ x: 'caught', y: 1 }] },
  ] } }
  const option = chartOption(saved) as Record<string, any>
  expect(option.series).toHaveLength(2)
  expect(option.series[0].type).toBe('pie')
  expect(option.series[0].data[0]).toMatchObject({ name: 'caught', value: 6 })
  expect(option.series[0].radius).not.toEqual(option.series[1].radius)
})

it('keeps sparse heatmap cells blank and includes phase in CSV', () => {
  const saved: SavedChart = { ...base, chart: { ...base.chart, metric: 'runs_per_100_legal_balls', group_by: 'bowler_phase', chart_type: 'heatmap', x_label: 'Bowler', y_label: 'Phase', value_label: 'Runs per 100 legal balls', series: [{ name: 'ODI', points: [
    { x: 'Bowler A', row: 'Powerplay', y: 120, runs: 12, sample_size: 10 },
    { x: 'Bowler A', row: 'Middle', y: null, runs: 2, sample_size: 3 },
  ] }] } }
  const option = chartOption(saved) as Record<string, any>
  expect(option.xAxis.data).toEqual(['Bowler A'])
  expect(option.yAxis.data).toEqual(['Powerplay', 'Middle'])
  expect(option.series[0].data).toHaveLength(1)
  expect(chartCsv(saved)).toContain('"Runs per 100 legal balls"')
  expect(chartCsv(saved)).toContain('"Phase"')
  expect(chartCsv(saved)).toContain('"Bowler A","","3","Middle"')
})

it('sizes bubbles by legal balls and leaves undefined averages in the table', () => {
  const saved: SavedChart = { ...base, chart: { ...base.chart, metric: 'batting_average', group_by: 'strike_rate', chart_type: 'bubble', x_label: 'Average', y_label: 'Rate', series: [{ name: 'India', points: [
    { x: 40, y: 90, size: 100, name: 'A', sample_size: 8 },
    { x: null, y: 80, size: 60, name: 'B', sample_size: 6 },
  ] }] } }
  const option = chartOption(saved) as Record<string, any>
  expect(option.series[0].data).toHaveLength(1)
  expect(option.series[0].data[0].value).toEqual([40, 90, 100])
  expect(option.series[0].label.show).toBe(false)
  expect(option.series[0].emphasis.label.show).toBe(true)
  expect(option.series[0].symbolSize([40, 90, 100])).toBeGreaterThan(option.series[0].symbolSize([40, 90, 60]))
  expect(chartCsv(saved)).toContain('"India","","80","6","B","60"')
})

it('uses separate innings axes for stacked run components', () => {
  const saved: SavedChart = { ...base, chart: { ...base.chart, metric: 'run_components', group_by: 'over', chart_type: 'stacked_area', x_label: 'Over', y_label: 'Runs', series: [
    { name: 'Home — Running', innings_number: 1, points: [{ x: 1, y: 3 }] },
    { name: 'Away — Running', innings_number: 2, points: [{ x: 1, y: 4 }] },
  ] } }
  const option = chartOption(saved) as Record<string, any>
  expect(option.grid).toHaveLength(2)
  expect(option.series.map((series: { stack: string; yAxisIndex: number }) => [series.stack, series.yAxisIndex])).toEqual([['innings-1', 0], ['innings-2', 1]])
})

it('stacks stand contributions and carries batter names to CSV', () => {
  const saved: SavedChart = { ...base, chart: { ...base.chart, metric: 'partnership_runs', group_by: 'stand', chart_type: 'stacked_bar', x_label: 'Stand', y_label: 'Runs', series: [
    { name: 'Batter A', points: [{ x: 'I1 · Stand 1', y: 30, batter_name: 'Player A' }] },
    { name: 'Batter B', points: [{ x: 'I1 · Stand 1', y: 20, batter_name: 'Player B' }] },
    { name: 'Extras', points: [{ x: 'I1 · Stand 1', y: 4, batter_name: 'Extras' }] },
  ] } }
  const option = chartOption(saved) as Record<string, any>
  expect(option.series.every((series: { stack: string }) => series.stack === 'partnership')).toBe(true)
  expect(option.series.map((series: { data: { value: number }[] }) => series.data[0].value)).toEqual([30, 20, 4])
  expect(chartCsv(saved)).toContain('"Player A"')
})
