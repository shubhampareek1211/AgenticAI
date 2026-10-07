import { describe, expect, it } from 'vitest'
import { chartOption } from './ChartPlot'
import type { SavedChart } from './api'

const base: SavedChart = {
  chart_id: 'chart', dataset_id: 'dataset', schema_version: 2,
  chart: { metric: 'runs', group_by: 'year', chart_type: 'bar', title: 'Runs by year', x_label: 'Year', y_label: 'Batter runs', series: [{ name: 'ODI', points: [{ x: '2024', y: 80 }, { x: '2025', y: 100 }] }] },
  coverage: {}, provenance: [],
}

describe('ECharts data mapping', () => {
  it('uses categorical integer years so the axis cannot invent fractional years', () => {
    const option = chartOption(base) as Record<string, any>
    expect(option.xAxis.type).toBe('category')
    expect(option.xAxis.data).toEqual(['2024', '2025'])
    expect(option.yAxis.min).toBe(0)
    expect(option.series[0].data.map((item: { value: number }) => item.value)).toEqual([80, 100])
  })

  it('keeps two innings on one date as two distinct values', () => {
    const option = chartOption({ ...base, chart: { ...base.chart, group_by: 'innings_date', chart_type: 'line', series: [{ name: 'ODI', points: [{ x: '2025-01-01', y: 20 }, { x: '2025-01-01', y: 54 }] }] } }) as Record<string, any>
    expect(option.xAxis.type).toBe('time')
    expect(option.series[0].data).toHaveLength(2)
    expect(option.series[0].data.map((item: { value: [string, number] }) => item.value[1])).toEqual([20, 54])
  })

  it.each([
    ['runs_per_over', 'bar'],
    ['cumulative_runs', 'line'],
  ] as const)('shows wicket markers and tooltip counts for %s', (metric, chart_type) => {
    const option = chartOption({ ...base, chart: { ...base.chart, metric, group_by: 'over', chart_type, x_label: 'Over', y_label: 'Runs', series: [
      { name: 'Home', points: [{ x: 10, y: 6, wickets: 2 }, { x: 2, y: 8, wickets: 0 }] },
      { name: 'Away', points: [{ x: 2, y: 7, wickets: 1 }] },
    ] } }) as Record<string, any>
    expect(option.xAxis.type).toBe('category')
    expect(option.xAxis.data).toEqual(['2', '10'])
    expect(option.series[0].data[1].label.formatter).toBe('● 2')
    expect(option.series[1].data[0].label.formatter).toBe('●')
    expect(option.series[0].data[0].label).toBeUndefined()
    const tip = option.tooltip.formatter({ seriesName: 'Home', axisValue: '10', value: 6, data: option.series[0].data[1] })
    expect(tip).toContain('Wickets: 2')
    expect(tip).toContain('Over: 10')
  })

  it('sorts rolling innings numerically and retains the baseline series', () => {
    const option = chartOption({ ...base, chart: { ...base.chart, metric: 'batting_average', group_by: 'rolling_innings', chart_type: 'line', series: [
      { name: 'ODI rolling average', points: [{ x: 10, y: 45, sample_size: 5 }, { x: 2, y: null, sample_size: 5 }] },
      { name: 'ODI imported-match baseline', points: [{ x: 2, y: 40 }, { x: 10, y: 40 }] },
    ] } }) as Record<string, any>
    expect(option.xAxis.data).toEqual(['2', '10'])
    expect(option.series[0].data.map((item: { value: number | null }) => item.value)).toEqual([null, 45])
    expect(option.series[1].data.map((item: { value: number }) => item.value)).toEqual([40, 40])
    expect(option.series[1].lineStyle.type).toBe('dashed')
  })

  it('preserves dismissal kind categories and format-separated series', () => {
    const option = chartOption({ ...base, chart: { ...base.chart, metric: 'dismissals', group_by: 'kind', chart_type: 'bar', series: [
      { name: 'ODI', points: [{ x: 'caught', y: 6 }, { x: 'bowled', y: 3 }] },
      { name: 'T20I', points: [{ x: 'caught', y: 2 }] },
    ] } }) as Record<string, any>
    expect(option.xAxis.data).toEqual(['caught', 'bowled'])
    expect(option.series[0].data.map((item: { value: number }) => item.value)).toEqual([6, 3])
    expect(option.series[1].data.map((item: { value: number | null }) => item.value)).toEqual([2, null])
  })
})
