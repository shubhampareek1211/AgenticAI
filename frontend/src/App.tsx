import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError, parseToolResult, type ToolCall, type ToolResult, type Transcript } from './api'
import { ChartCard } from './ChartCard'
import { initialSession, saveSession, sessionLink } from './session'
import { useVoiceInput } from './useVoiceInput'
import { VoiceInput } from './VoiceInput'
import { useSpeechPlayback } from './useSpeechPlayback'

const examples = [
  'Show Virat Kohli’s ODI dismissal kinds as a donut chart',
  'Draw Virat Kohli’s ODI batting-rate heatmap by bowler and match phase',
  'Compare India ODI batters in a bubble chart of average versus scoring rate',
  'Load Cricsheet match 1022353 and show its run components by over',
  'Show the partnership contributions for Cricsheet match 1022353',
  'How did Virat Kohli score around teammate wickets in ODIs?',
]

function text(value: unknown): string | null { return typeof value === 'string' && value.trim() ? value : null }
function number(value: unknown): string | null { return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : null }
function appendSpoken(draft: string, spoken: string): string { return draft ? `${draft.trimEnd()} ${spoken}` : spoken }

function Coverage({ result }: { result: ToolResult }) {
  const coverage = result.coverage || {}
  const parts: string[] = []
  const sample = coverage.sample_size as Record<string, unknown> | undefined
  if (number(sample?.matches)) parts.push(`${number(sample?.matches)} available matches`)
  if (number(sample?.batting_innings)) parts.push(`${number(sample?.batting_innings)} batting innings`)
  if (number(coverage.eligible_events)) parts.push(`${number(coverage.eligible_events)} eligible wicket events`)
  if (text(coverage.date_start) && text(coverage.date_end)) parts.push(`${coverage.date_start} to ${coverage.date_end}`)
  if (text(coverage.scope)) parts.push(String(coverage.scope))
  if (text(coverage.selection_bias)) parts.push(String(coverage.selection_bias))
  if (coverage.insufficient_sample) parts.push('Small sample')
  if (!parts.length) return null
  return <div className="source-notes">{parts.map((part, index) => <span key={index}>{part}</span>)}</div>
}

function PlayerCard({ result, onFollowup, disabled }: { result: ToolResult; onFollowup: (message: string) => void; disabled: boolean }) {
  const data = result.data || {}
  const identity = (data.identity || {}) as Record<string, unknown>
  const filters = (data.filters || {}) as Record<string, unknown>
  const [format, setFormat] = useState(String(filters.format || 'all'))
  const [start, setStart] = useState(String(filters.start_date || ''))
  const [end, setEnd] = useState(String(filters.end_date || ''))
  const name = text(identity.espn_display_name) || text(identity.name) || 'Player'
  const playerId = text(identity.player_id)
  const stats = (data.stats || {}) as Record<string, unknown>
  const coverageByFormat = ((result.coverage || {}).formats || {}) as Record<string, Record<string, unknown>>
  const apply = () => {
    if (!playerId || (start && end && start > end)) return
    const formatText = format === 'all' ? 'ODI and T20I' : format.toUpperCase()
    onFollowup(`Get updated player data for ${name} (player_id ${playerId}) using ${formatText} matches${start ? ` from ${start}` : ''}${end ? ` through ${end}` : ''}. Summarize the selected coverage and statistics.`)
  }
  return <section className="result-card player-card" aria-label={`${name} player data`}>
    <div className="card-header"><div><span className="eyebrow">PLAYER PROFILE</span><h3>{name}</h3></div><span className="badge">{Object.keys(stats).map(value => value.toUpperCase()).join(' · ') || 'Cricsheet'}</span></div>
    {playerId && <p className="fineprint">Cricsheet player ID: {playerId}</p>}
    <div className="player-stats">{Object.entries(stats).map(([kind, raw]) => {
      const values = raw as Record<string, Record<string, unknown>>
      const batting = values.batting || {}
      const bowling = values.bowling || {}
      return <div key={kind} className="stat-format"><strong>{kind.toUpperCase()}</strong><span><b>{number(batting.runs) ?? '—'}</b> runs</span><span><b>{number(coverageByFormat[kind]?.batting_innings) ?? '—'}</b> innings</span><span><b>{number(bowling.wickets) ?? '—'}</b> wickets</span></div>
    })}</div>
    <Coverage result={result} />
    <div className="filter-panel"><strong>Refine the analysis</strong><div className="filter-grid">
      <label>Format<select value={format} onChange={event => setFormat(event.target.value)}><option value="all">ODI + T20I</option><option value="odi">ODI</option><option value="t20i">T20I</option></select></label>
      <label>From<input type="date" value={start} onChange={event => setStart(event.target.value)} /></label>
      <label>Through<input type="date" value={end} onChange={event => setEnd(event.target.value)} /></label>
      <button type="button" className="small-primary" disabled={disabled || !playerId || Boolean(start && end && start > end)} onClick={apply}>Apply filters</button>
    </div>{start && end && start > end && <p className="field-error">Start date must be on or before end date.</p>}</div>
  </section>
}

function WicketCard({ result }: { result: ToolResult }) {
  const data = result.data || {}
  const rates = (data.rates || {}) as Record<string, Record<string, unknown>>
  const before = rates.before || {}
  const after = rates.after || {}
  return <section className="result-card" aria-label="Wicket response analysis"><span className="eyebrow">WICKET RESPONSE</span><h3>{text(data.player_name) || 'Batting around a wicket'}</h3><div className="metric-pair"><div><span>Before</span><strong>{number(before.runs_per_100_balls) ?? '—'}</strong><small>runs / 100 legal balls</small></div><div><span>After</span><strong>{number(after.runs_per_100_balls) ?? '—'}</strong><small>runs / 100 legal balls</small></div></div><Coverage result={result} /></section>
}

function ToolCard({ call, sessionId, onFollowup, disabled }: { call: ToolCall; sessionId: string; onFollowup: (message: string) => void; disabled: boolean }) {
  const result = parseToolResult(call.result)
  const chartId = call.name === 'create_cricket_chart' && result?.ok ? text(result.data?.chart_id) : null
  const isPlayer = Boolean(call.name === 'get_player_data' && result?.ok && result.data?.identity)
  const isWicket = Boolean(call.name === 'analyze_wicket_response' && result?.ok && result.data?.rates)
  const label = call.name.replaceAll('_', ' ')
  return <div className="tool-group" data-tool-id={call.id}>
    <div className={`tool-progress ${call.status}`} role="status"><span className="progress-dot" />{label} <span className="progress-status">{call.status === 'requested' ? 'running' : call.status}</span></div>
    {isPlayer && result && <PlayerCard result={result} onFollowup={onFollowup} disabled={disabled} />}
    {isWicket && result && <WicketCard result={result} />}
    {chartId && <ChartCard sessionId={sessionId} chartId={chartId} />}
    {call.status !== 'requested' && result && !result.ok && <div className="tool-error">{result.error?.message || 'The tool could not complete this request.'}</div>}
    <details className="tool-details"><summary>Tool details</summary><div><strong>{call.name}</strong><pre>{JSON.stringify(call.args, null, 2)}</pre><pre>{call.result}</pre></div></details>
  </div>
}

export function App({ authEmail, onSignOut }: { authEmail?: string; onSignOut?: () => void } = {}) {
  const [sessionId, setSessionId] = useState<string | null>(initialSession)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [draft, setDraft] = useState('')
  const [overflowTranscript, setOverflowTranscript] = useState('')
  const [pending, setPending] = useState<{ message: string; afterOrdinal: number } | null>(null)
  const [sending, setSending] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [error, setError] = useState('')
  const [restoring, setRestoring] = useState(Boolean(sessionId))
  const [linkCopied, setLinkCopied] = useState(false)
  const [autoRead, setAutoRead] = useState(false)
  const autoReadRef = useRef(false)
  const sessionRef = useRef(sessionId)
  const sendingRef = useRef(false)
  const clearingRef = useRef(false)
  const bottom = useRef<HTMLDivElement>(null)
  const scrollArea = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const recoveringRef = useRef(false)
  const polledSessionRef = useRef<string | null>(null)
  const previousSendingRef = useRef(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const playback = useSpeechPlayback()
  const draftRef = useRef(draft)
  draftRef.current = draft
  const voice = useVoiceInput(spoken => {
    const combined = appendSpoken(draftRef.current, spoken)
    if (combined.length > 8000) { setOverflowTranscript(spoken); return }
    setDraft(combined)
    window.requestAnimationFrame(() => inputRef.current?.focus())
  })

  const remember = (id: string | null) => { sessionRef.current = id; setSessionId(id); saveSession(id) }
  const refresh = async (id: string) => {
    const latest = await api.transcript(id)
    if (sessionRef.current !== id) return latest
    setTranscript(latest)
    setRestoring(false)
    setPending(current => {
      if (!current) return null
      const persisted = latest.messages.some(item => item.role === 'user' && item.ordinal > current.afterOrdinal && item.payload.content === current.message)
      if (persisted) return null
      if (latest.request_state === 'idle' && !sendingRef.current) { setDraft(current.message); return null }
      return current
    })
    if (latest.request_state === 'interrupted' && !recoveringRef.current) {
      recoveringRef.current = true; setRecovering(true); setUncertain(true)
      try {
        await api.recover(id)
        const recovered = await api.transcript(id)
        if (sessionRef.current === id) { setTranscript(recovered); if (recovered.request_state !== 'interrupted') setUncertain(false) }
      } catch (reason) { if (!(reason instanceof ApiError && reason.status === 409)) throw reason }
      finally { recoveringRef.current = false; setRecovering(false) }
    } else if (latest.request_state !== 'interrupted') setUncertain(false)
    return latest
  }

  useEffect(() => {
    if (!sessionId) { polledSessionRef.current = null; previousSendingRef.current = false; setRestoring(false); return }
    let active = true
    let working = false
    const firstForSession = polledSessionRef.current !== sessionId
    const startedSending = sending && !previousSendingRef.current
    polledSessionRef.current = sessionId
    previousSendingRef.current = sending
    const tick = async () => {
      if (working) return
      working = true
      try { await refresh(sessionId); if (active) setError('') }
      catch (reason) {
        if (!active || sessionRef.current !== sessionId) return
        if (reason instanceof ApiError && reason.status === 404) { remember(null); setTranscript(null); setError('Saved conversation was not found. Start a new one.') }
        else if (reason instanceof ApiError && reason.status === 403) { remember(null); setTranscript(null); setError('This conversation is unavailable to your account. Start a new one.') }
        else { setUncertain(true); setError(reason instanceof Error ? `Could not load saved progress: ${reason.message}` : 'Could not load saved progress.') }
        setRestoring(false)
      } finally { working = false }
    }
    if (firstForSession || startedSending) void tick()
    const interval = sending || uncertain || transcript?.request_state === 'running' || transcript?.request_state === 'interrupted' ? window.setInterval(tick, 1000) : null
    return () => { active = false; if (interval) window.clearInterval(interval) }
    // The state fields determine whether polling is needed. refresh reads current sessionRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, sending, uncertain, transcript?.request_state])

  useEffect(() => { if (nearBottom.current) bottom.current?.scrollIntoView({ block: 'end' }) }, [transcript, pending, sending])

  const submit = async (raw: string) => {
    const message = raw.trim()
    if (!message || message.length > 8000 || voice.activeRef.current !== 'idle' || sendingRef.current || clearingRef.current || recoveringRef.current || uncertain || transcript?.request_state === 'running' || transcript?.request_state === 'interrupted' || restoring) return
    playback.stop()
    sendingRef.current = true; setSending(true); setError(''); setDraft('')
    const afterOrdinal = Math.max(0, ...(transcript?.messages.map(item => item.ordinal) || []))
    setPending({ message, afterOrdinal })
    let id = sessionRef.current
    try {
      if (!id) { id = await api.allocate(); remember(id); setTranscript(null) }
      await api.chat(id, message)
      const latest = await refresh(id)
      setPending(null)
      if (autoReadRef.current && latest.request_state === 'idle') {
        const answer = latest.messages.filter(item => item.role === 'assistant' && item.ordinal > afterOrdinal && item.payload.content).at(-1)
        if (answer) playback.speak(answer.id, answer.payload.content || '', id)
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 403) {
        remember(null); setTranscript(null); setPending(null); setDraft(message)
        setError('This conversation is unavailable to your account. Start a new one.')
        return
      }
      if (id) {
        try {
          const latest = await refresh(id)
          const persisted = latest.messages.some(item => item.role === 'user' && item.ordinal > afterOrdinal && item.payload.content === message)
          if (persisted) setPending(null)
          else { setDraft(message); setPending(null) }
          if (latest.request_state === 'running') setError('The server is still working. Saved progress will appear here.')
          else if (reason instanceof ApiError && reason.status === 409) setError('This conversation is busy. Wait for the current response before sending another message.')
          else if (!persisted) setError(reason instanceof Error ? reason.message : 'Could not send message.')
        } catch { setUncertain(true); setError('Connection lost. Waiting for saved progress before another request can be sent.') }
      } else { setDraft(message); setPending(null); setError(reason instanceof Error ? reason.message : 'Could not start a conversation.') }
    } finally { sendingRef.current = false; setSending(false) }
  }

  const conversationBusy = sending || clearing || uncertain || recovering || restoring || transcript?.request_state === 'running' || transcript?.request_state === 'interrupted'
  const busy = conversationBusy || voice.active
  const newConversation = () => { if (conversationBusy) return; voice.cancel(); playback.stop(); remember(null); setTranscript(null); setDraft(''); setOverflowTranscript(''); setPending(null); setError('') }
  const clearConversation = async () => {
    if (conversationBusy || clearingRef.current || !sessionId || !window.confirm('Delete this conversation and its saved charts?')) return
    voice.cancel()
    playback.stop()
    setOverflowTranscript('')
    clearingRef.current = true; setClearing(true)
    try { await api.clear(sessionId); remember(null); setTranscript(null); setPending(null); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clear conversation.') }
    finally { clearingRef.current = false; setClearing(false) }
  }
  const copyLink = async () => {
    if (!sessionId) return
    try { await navigator.clipboard.writeText(sessionLink(sessionId)); setLinkCopied(true); window.setTimeout(() => setLinkCopied(false), 2500) }
    catch { setError('Could not copy the conversation link.') }
  }

  const toolMap = new Map(transcript?.tool_calls.map(call => [call.id, call]) || [])
  const persistedPending = pending && transcript?.messages.some(item => item.role === 'user' && item.ordinal > pending.afterOrdinal && item.payload.content === pending.message)
  const displayMessages = transcript?.messages.filter(item => item.role === 'user' || (item.role === 'assistant' && item.payload.content) || (item.role === 'tool' && item.payload.tool_call_id && toolMap.has(item.payload.tool_call_id))) || []

  return <div className="app-shell">
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">✦</span><div><strong>Cricket Analyst</strong><span>Evidence from available match data</span></div></div><nav aria-label="Conversation actions">{authEmail && <span className="account-email">{authEmail}</span>}<button type="button" className="header-action" onClick={copyLink} disabled={!sessionId}>{linkCopied ? 'Copied' : 'Copy link'}</button><button type="button" className="header-action" onClick={newConversation} disabled={conversationBusy}>New conversation</button><button type="button" className="header-action danger" onClick={clearConversation} disabled={!sessionId || conversationBusy}>Clear</button>{onSignOut && <button type="button" className="header-action" onClick={() => { playback.stop(); voice.cancel(); onSignOut() }}>Sign out</button>}</nav></header>
    <main className="conversation" ref={scrollArea} onScroll={event => { const target = event.currentTarget; nearBottom.current = target.scrollHeight - target.scrollTop - target.clientHeight < 100 }}>
      <div className="conversation-inner">
        {displayMessages.length === 0 && !pending && !restoring && <div className="welcome"><div className="welcome-symbol">✦</div><span className="eyebrow">ASK THE CRICKET DATA</span><h1>Find the story in the score.</h1><p>Explore a player’s batting record, compare trends, and inspect how the answer was calculated. Results reflect the imported Cricsheet sample.</p><div className="examples">{examples.map(prompt => <button type="button" key={prompt} onClick={() => void submit(prompt)} disabled={busy}>{prompt}<span aria-hidden="true">↗</span></button>)}</div></div>}
        {restoring && <div className="status-line" role="status">Restoring saved conversation…</div>}
        {displayMessages.map(item => {
          if (item.role === 'tool') {
            const call = toolMap.get(item.payload.tool_call_id!)!
            return <ToolCard key={item.id} call={call} sessionId={sessionId!} onFollowup={message => void submit(message)} disabled={busy} />
          }
          return <article className={`message ${item.role}`} key={item.id}><div className="message-heading"><span className="message-role">{item.role === 'user' ? 'YOU' : 'ANALYST'}</span>{item.role === 'assistant' && playback.supported && <button type="button" className="read-answer" onClick={() => playback.speakingId === item.id ? playback.stop() : playback.speak(item.id, item.payload.content || '', sessionId || undefined)} aria-label={playback.speakingId === item.id ? 'Stop reading answer' : 'Read answer aloud'}>{playback.speakingId === item.id ? 'Stop audio' : 'Read aloud'}</button>}</div>{item.role === 'assistant' ? <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: props => <a {...props} target="_blank" rel="noopener noreferrer" /> }}>{item.payload.content}</ReactMarkdown></div> : <p>{item.payload.content}</p>}</article>
        })}
        {pending && !persistedPending && <article className="message user pending-message"><span className="message-role">YOU</span><p>{pending.message}</p></article>}
        {(sending || transcript?.request_state === 'running') && <div className="thinking" role="status"><span className="thinking-pulse" />{transcript?.tool_calls.some(call => call.status === 'requested') ? 'Working with cricket data…' : 'Thinking…'}</div>}
        {error && <div className="banner-error" role="alert">{error}</div>}
        <div ref={bottom} />
      </div>
    </main>
    <footer className="composer-wrap">
      <div className="voice-options"><VoiceInput enabled={voice.enabled} languages={voice.languages} language={voice.language} onLanguageChange={voice.setLanguage} phase={voice.phase} elapsedSeconds={voice.elapsedSeconds} error={voice.error} disabled={conversationBusy || Boolean(overflowTranscript)} onRecord={() => { playback.stop(); void voice.start() }} onStop={voice.stop} onCancel={() => voice.cancel()} />{playback.supported && <label className="read-aloud-option"><input type="checkbox" checked={autoRead} onChange={event => { autoReadRef.current = event.target.checked; setAutoRead(event.target.checked); if (!event.target.checked) playback.stop() }} />Read answers aloud</label>}{playback.speakingId && <button type="button" className="stop-speech" onClick={playback.stop}>Stop speaking</button>}{playback.status && <span role="status">{playback.status}</span>}{playback.notice && <span role="alert">{playback.notice}</span>}</div>
      {overflowTranscript && <div className="voice-overflow"><label htmlFor="voice-overflow-text">The transcript does not fit in your draft. Edit either text, then add it:</label><textarea id="voice-overflow-text" value={overflowTranscript} maxLength={8000} onChange={event => setOverflowTranscript(event.target.value)} rows={2} /><div><button type="button" onClick={() => { const combined = appendSpoken(draftRef.current, overflowTranscript); if (combined.length > 8000) return; setDraft(combined); setOverflowTranscript(''); window.requestAnimationFrame(() => inputRef.current?.focus()) }} disabled={appendSpoken(draft, overflowTranscript).length > 8000}>Add to draft</button><button type="button" onClick={() => setOverflowTranscript('')}>Dismiss transcript</button></div></div>}
      <form className="composer" onSubmit={event => { event.preventDefault(); void submit(draft) }}><label htmlFor="message-input" className="sr-only">Message Cricket Analyst</label><textarea ref={inputRef} id="message-input" placeholder="Ask about a player, trend, or wicket response…" value={draft} maxLength={8000} rows={2} readOnly={voice.active} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(draft) } }} /><button type="submit" disabled={!draft.trim() || busy} aria-label="Send message">Send <span aria-hidden="true">↗</span></button></form><p>Cricsheet sample · Verify sample size and coverage before drawing conclusions</p>
    </footer>
  </div>
}
