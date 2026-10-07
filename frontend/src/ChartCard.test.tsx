import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type SavedChart } from './api'
import { ChartCard, chartCsv } from './ChartCard'

vi.mock('./ChartPlot', () => ({ default: () => <div>Chart renderer</div> }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const saved: SavedChart = {
  chart_id: 'chart', dataset_id: 'dataset', schema_version: 2,
  chart: { metric: 'batting_average', group_by: 'year', chart_type: 'bar', title: 'Batting average by year', x_label: 'Year', y_label: 'Runs per dismissal', series: [{ name: 'ODI', points: [{ x: '2024', y: 45, sample_size: 5 }, { x: '2025', y: null, sample_size: 2 }] }] },
  coverage: { plotted: { batting_innings: 7, excluded_incomplete_batting_innings: 1 } }, provenance: [],
}

describe('ChartCard', () => {
  it('loads normalized chart values and offers an accessible data table', async () => {
    vi.spyOn(api, 'chart').mockResolvedValue(saved)
    render(<ChartCard sessionId="session" chartId="chart" />)
    expect(await screen.findByText('Batting average by year')).toBeTruthy()
    expect(screen.getByText('1 incomplete innings excluded')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Data table' }))
    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy())
    expect(screen.getByText('Unavailable')).toBeTruthy()
    expect(api.chart).toHaveBeenCalledWith('session', 'chart')
  })

  it('exports exact values and shields CSV cells from formula evaluation', () => {
    const csv = chartCsv({ ...saved, chart: { ...saved.chart, series: [{ name: '=unsafe', points: [{ x: '2025', y: null }] }] } })
    expect(csv).toContain('"\'=unsafe"')
    expect(csv).toContain('"2025","",""')
  })

  it('restores focus after the expanded dialog closes', async () => {
    vi.spyOn(api, 'chart').mockResolvedValue(saved)
    render(<ChartCard sessionId="session" chartId="chart" />)
    const expand = await screen.findByRole('button', { name: /Expand/ })
    fireEvent.click(expand)
    const dialog = screen.getByRole('dialog')
    expect(dialog.contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(dialog.contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /Expand/ }))
  })

  it('offers wicket counts in the accessible table and CSV for match charts', async () => {
    const match: SavedChart = { ...saved, coverage: { method: 'Pre/post innings penalty runs are excluded.' }, chart: { ...saved.chart, metric: 'runs_per_over', group_by: 'over', chart_type: 'bar', title: 'Manhattan', x_label: 'Over', y_label: 'Runs', series: [{ name: 'Home', points: [{ x: 1, y: 8, wickets: 2 }, { x: 2, y: 4, wickets: 0 }] }] } }
    vi.spyOn(api, 'chart').mockResolvedValue(match)
    render(<ChartCard sessionId="session" chartId="chart" />)
    expect(await screen.findByText('Manhattan')).toBeTruthy()
    expect(screen.getByText(/Red dots mark wickets/)).toBeTruthy()
    expect(screen.getByText('Pre/post innings penalty runs are excluded.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Data table' }))
    const table = screen.getByRole('table')
    expect(table.textContent).toContain('Wickets')
    expect(table.textContent).toContain('2')
    expect(chartCsv(match)).toContain('"Wickets"')
    expect(chartCsv(match)).toContain('"Home","1","8","","2"')
    expect(chartCsv(match)).toContain('"Home","2","4","","0"')
  })
})
