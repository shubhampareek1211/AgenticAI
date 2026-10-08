import type { VoicePhase } from './useVoiceInput'

interface Props {
  enabled: boolean
  phase: VoicePhase
  elapsedSeconds: number
  error: string
  languages: string[]
  language: string
  onLanguageChange: (language: string) => void
  disabled: boolean
  onRecord: () => void
  onStop: () => void
  onCancel: () => void
}

const languageLabels: Record<string, string> = { en: 'English', hi: 'Hindi', auto: 'Auto-detect' }

export function VoiceInput({ enabled, phase, elapsedSeconds, error, languages, language, onLanguageChange, disabled, onRecord, onStop, onCancel }: Props) {
  if (!enabled) return null
  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = String(elapsedSeconds % 60).padStart(2, '0')
  return <div className="voice-input">
    <div className="voice-controls">
      {languages.length > 1 && <label className="voice-language">Recording language <select value={language} onChange={event => onLanguageChange(event.target.value)} disabled={phase !== 'idle' || disabled}>{languages.map(code => <option value={code} key={code}>{languageLabels[code] || code}</option>)}</select></label>}
      {phase === 'idle' && <button type="button" className="voice-button" onClick={onRecord} disabled={disabled} aria-label="Record a question">🎙 <span>Record</span></button>}
      {phase === 'requesting_permission' && <><span className="voice-status" role="status">Waiting for microphone permission…</span><button type="button" className="voice-secondary" onClick={onCancel}>Cancel</button></>}
      {phase === 'recording' && <><span className="voice-status recording"><span className="recording-dot" aria-hidden="true" />Recording <time>{minutes}:{seconds}</time></span><button type="button" className="voice-button" onClick={onStop} aria-label="Stop recording and transcribe">Stop</button><button type="button" className="voice-secondary" onClick={onCancel}>Cancel</button></>}
      {phase === 'transcribing' && <><span className="voice-status" role="status">Transcribing your question…</span><button type="button" className="voice-secondary" onClick={onCancel}>Cancel</button></>}
    </div>
    <span className="sr-only" aria-live="polite">{phase === 'recording' ? 'Recording started' : phase === 'idle' ? '' : phase === 'transcribing' ? 'Transcribing your question' : 'Waiting for microphone permission'}</span>
    {error && <p className="voice-error" role="alert">{error}</p>}
    {language === 'auto' && <span className="voice-language-note">Detects the main language in each recording; Hindi-English mixing may need edits.</span>}
  </div>
}
