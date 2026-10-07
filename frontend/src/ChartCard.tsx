import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { api, type SavedChart } from './api'

const ChartPlot = lazy(() => import('./ChartPlot'))

function count(value: unknown): string | null {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : null
}

export function chartContext(saved: SavedChart): string[] {
  const coverage = saved.coverage || {}
  const plotted = (coverage.plotted || {}) as Record<string, unknown>
  const lines: string[] = []
  if (count(plotted.batting_innings)) lines.push(`${count(plotted.batting_innings)} complete batting innings`)
  if (count(plotted.matches)) lines.push(`${count(plotted.matches)} matches`)
  if (count(plotted.eligible_events)) lines.push(`${count(plotted.eligible_events)} eligible wicket events`)
  if (count(plotted.innings)) lines.push(`${count(plotted.innings)} complete regular innings`)
  if (count(plotted.window_size)) lines.push(`${count(plotted.window_size)} innings per rolling window`)
  if (count(plotted.players)) lines.push(`${count(plotted.players)} qualifying players`)
  if (count(plotted.bowlers)) lines.push(`${count(plotted.bowlers)} bowlers`)
  if (count(plotted.stands)) lines.push(`${count(plotted.stands)} partnerships`)
  if (typeof plotted.undefined_average === 'number' && plotted.undefined_average > 0) lines.push(`${count(plotted.undefined_average)} players have no defined batting average and appear in the table only`)
  if (plotted.date_start && plotted.date_end) lines.push(`${plotted.date_start} to ${plotted.date_end}`)
  else if (coverage.date_start && coverage.date_end) lines.push(`${coverage.date_start} to ${coverage.date_end}`)
  const excluded = count(plotted.excluded_incomplete_batting_innings)
  if (excluded && excluded !== '0') lines.push(`${excluded} incomplete innings excluded`)
  const excludedMatch = count(plotted.excluded_incomplete_or_super_over_innings)
  if (excludedMatch && excludedMatch !== '0') lines.push(`${excludedMatch} incomplete or super-over innings excluded`)
  if (plotted.insufficient_sample || coverage.insufficient_sample) lines.push('Small sample: interpret this comparison cautiously')
  if (typeof coverage.scope === 'string') lines.push(coverage.scope)
  if (typeof coverage.method === 'string') lines.push(coverage.method)
  if (typeof plotted.method === 'string' && plotted.method !== coverage.method) lines.push(plotted.method)
  if (typeof coverage.selection_bias === 'string') lines.push(coverage.selection_bias)
  if (typeof plotted.wicket_method === 'string') lines.push(plotted.wicket_method)
  const sourceCount = saved.provenance?.filter(source => source.provider === 'cricsheet').length || 0
  if (sourceCount) lines.push(`Source: ${sourceCount} Cricsheet import${sourceCount === 1 ? '' : 's'}`)
  if (saved.chart.group_by === 'over' && ['runs_per_over', 'cumulative_runs'].includes(saved.chart.metric)) lines.push('Red dots mark wickets; a number marks multiple wickets in an over')
  return lines
}

function hasWicketCounts(saved: SavedChart): boolean {
  return saved.chart.series.some(series => series.points.some(point => typeof point.wickets === 'number'))
}

function extraColumns(saved: SavedChart): [string, string][] {
  if (saved.chart.chart_type === 'heatmap') return [['row', 'Phase']]
  if (saved.chart.chart_type === 'bubble') return [['name', 'Player'], ['size', 'Legal balls']]
  if (saved.chart.chart_type === 'stacked_bar') return [['batter_name', 'Batter']]
  return []
}

function csvCell(value: unknown): string {
  const text = value == null ? '' : String(value)
  const safe = /^[=+@\-\t\r]/.test(text) ? `'${text}` : text
  return `"${safe.replaceAll('"', '""')}"`
}

export function chartCsv(saved: SavedChart): string {
  const wickets = hasWicketCounts(saved)
  const extra = extraColumns(saved)
  const rows = [['Series', saved.chart.x_label, saved.chart.value_label ?? saved.chart.y_label, 'Sample size', ...extra.map(([, label]) => label), ...(wickets ? ['Wickets'] : [])]]
  for (const series of saved.chart.series) {
    for (const point of series.points) rows.push([series.name, point.x == null ? '' : String(point.x), point.y == null ? '' : String(point.y), point.sample_size == null ? '' : String(point.sample_size), ...extra.map(([key]) => point[key] == null ? '' : String(point[key])), ...(wickets ? [point.wickets == null ? '' : String(point.wickets)] : [])])
  }
  return rows.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

function download(name: string, data: Blob): void {
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function sourceUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try { const url = new URL(value); return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null }
  catch { return null }
}

export function ChartCard({ sessionId, chartId }: { sessionId: string; chartId: string }) {
  const [saved, setSaved] = useState<SavedChart | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [showTable, setShowTable] = useState(false)
  const [svg, setSvg] = useState<(() => string) | null>(null)
  const expandButton = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const opened = useRef(false)
  const onSvgReady = useCallback((getSvg: (() => string) | null) => setSvg(getSvg ? () => getSvg : null), [])

  useEffect(() => {
    if (expanded) opened.current = true
    else if (opened.current) expandButton.current?.focus()
  }, [expanded])

  const dialogKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') { event.preventDefault(); setExpanded(false); return }
    if (event.key !== 'Tab' || !dialog.current) return
    const controls = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
    const first = controls[0], last = controls[controls.length - 1]
    if (!first || !last) return
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }

  useEffect(() => {
    let mounted = true
    api.chart(sessionId, chartId).then(value => { if (mounted) setSaved(value) }).catch(reason => { if (mounted) setError(reason instanceof Error ? reason.message : 'Chart unavailable') })
    return () => { mounted = false }
  }, [sessionId, chartId])

  if (error) return <div className="chart-card error" role="alert">Chart unavailable: {error}</div>
  if (!saved) return <div className="chart-card chart-loading" role="status">Loading chart…</div>
  const chart = saved.chart
  const wickets = hasWicketCounts(saved)
  const extra = extraColumns(saved)
  const points = chart.series.reduce((total, series) => total + series.points.length, 0)
  const content = (
    <>
      <div className="chart-topline">
        <div><span className="eyebrow">CRICKET DATA</span><h3>{chart.title}</h3></div>
        <div className="chart-actions">
          <button type="button" className="text-button" onClick={() => setShowTable(value => !value)} aria-expanded={showTable}>{showTable ? 'Hide table' : 'Data table'}</button>
          <button type="button" className="text-button" onClick={() => download(`${chart.metric}-${chart.group_by}.csv`, new Blob([chartCsv(saved)], { type: 'text/csv;charset=utf-8' }))}>CSV</button>
          <button type="button" className="text-button" disabled={!svg} onClick={() => { const data = svg?.(); if (data) { const link = document.createElement('a'); link.href = data; link.download = `${chart.metric}-${chart.group_by}.svg`; link.click() } }}>SVG</button>
          {!expanded && <button ref={expandButton} type="button" className="text-button" onClick={() => setExpanded(true)}>Expand ↗</button>}
        </div>
      </div>
      <div className="plot-shell" role="img" aria-label={`${chart.title}. ${points} data points.${wickets ? ' Red dots mark wickets.' : ''} Data table is available.`}>
        <Suspense fallback={<div className="plot-loading">Loading visualization…</div>}><ChartPlot saved={saved} onSvgReady={onSvgReady} /></Suspense>
      </div>
      <div className="chart-context">{chartContext(saved).map((line, index) => <span key={index}>{line}</span>)}</div>
      {saved.provenance?.length > 0 && <details className="source-details"><summary>Sources and method</summary><ul>{saved.provenance.map((source, index) => {
        const url = sourceUrl(source.url)
        return <li key={index}>{String(source.provider || 'Data source')}{url && <> · <a href={url} target="_blank" rel="noopener noreferrer">Source file</a></>}{typeof source.revision === 'string' && ` · revision ${source.revision}`}</li>
      })}</ul>{typeof saved.coverage?.method === 'string' && <p>{saved.coverage.method}</p>}</details>}
      {showTable && <div className="table-scroll"><table><caption>{chart.title}: exact chart values</caption><thead><tr><th scope="col">Series</th><th scope="col">{chart.x_label}</th><th scope="col">{chart.value_label ?? chart.y_label}</th><th scope="col">Sample</th>{extra.map(([key, label]) => <th key={key} scope="col">{label}</th>)}{wickets && <th scope="col">Wickets</th>}</tr></thead><tbody>{chart.series.flatMap(series => series.points.map((point, index) => <tr key={`${series.name}-${index}`}><th scope="row">{series.name}</th><td>{point.x ?? 'Unavailable'}</td><td>{point.y == null ? 'Unavailable' : point.y}</td><td>{point.sample_size ?? '—'}</td>{extra.map(([key]) => <td key={key}>{point[key] == null ? '—' : String(point[key])}</td>)}{wickets && <td>{point.wickets ?? '—'}</td>}</tr>))}</tbody></table></div>}
    </>
  )
  return <>
    {!expanded && <section className="chart-card" aria-label={chart.title}>{content}</section>}
    {expanded && <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setExpanded(false) }}><div ref={dialog} className="chart-dialog" role="dialog" aria-modal="true" aria-label={`${chart.title}, expanded`} onKeyDown={dialogKeys}><button type="button" className="dialog-close" autoFocus onClick={() => setExpanded(false)} aria-label="Close expanded chart">×</button>{content}</div></div>}
  </>
}
