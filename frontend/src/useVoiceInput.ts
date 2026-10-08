import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type VoiceConfig } from './api'
import { loadConfigWithRetry } from './configRetry'

export type VoicePhase = 'idle' | 'requesting_permission' | 'recording' | 'transcribing'

const recordingTypes = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']

function voiceError(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === 'NotAllowedError') return 'Microphone permission was denied. You can still type your question.'
  if (reason instanceof DOMException && reason.name === 'NotFoundError') return 'No microphone was found. You can still type your question.'
  if (reason instanceof ApiError) return reason.message
  return reason instanceof Error ? reason.message : 'Voice input failed. Please try again.'
}

export function useVoiceInput(onTranscript: (text: string) => void) {
  const [config, setConfig] = useState<VoiceConfig | null>(null)
  const [language, setLanguageState] = useState('en')
  const [phase, setPhase] = useState<VoicePhase>('idle')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [error, setError] = useState('')
  const configRef = useRef<VoiceConfig | null>(null)
  const languageRef = useRef('en')
  const phaseRef = useRef<VoicePhase>('idle')
  const operationRef = useRef(0)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const intervalRef = useRef<number | null>(null)
  const limitRef = useRef<number | null>(null)
  const onTranscriptRef = useRef(onTranscript)
  onTranscriptRef.current = onTranscript

  const changePhase = (next: VoicePhase) => { phaseRef.current = next; setPhase(next) }
  const setLanguage = (next: string) => {
    if (phaseRef.current !== 'idle' || !configRef.current?.languages.includes(next)) return
    languageRef.current = next
    setLanguageState(next)
  }
  const clearTimers = () => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current)
    if (limitRef.current !== null) window.clearTimeout(limitRef.current)
    intervalRef.current = null
    limitRef.current = null
  }
  const stopTracks = () => {
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null
  }
  const disposeRecorder = () => {
    const recorder = recorderRef.current
    if (recorder) {
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
      if (recorder.state !== 'inactive') { try { recorder.stop() } catch { /* Already stopping. */ } }
    }
    recorderRef.current = null
  }
  const cancel = (update = true) => {
    operationRef.current += 1
    clearTimers()
    requestRef.current?.abort()
    requestRef.current = null
    disposeRecorder()
    stopTracks()
    phaseRef.current = 'idle'
    if (update) { setPhase('idle'); setElapsedSeconds(0); setError('') }
  }
  const fail = (reason: unknown, operation: number) => {
    if (operationRef.current !== operation) return
    cancel()
    setError(voiceError(reason))
  }

  const stop = () => {
    if (phaseRef.current !== 'recording') return
    const recorder = recorderRef.current
    if (!recorder) return
    clearTimers()
    changePhase('transcribing')
    try { recorder.stop(); stopTracks() }
    catch (reason) { fail(reason, operationRef.current) }
  }

  const start = async () => {
    if (phaseRef.current !== 'idle' || !configRef.current?.enabled) return
    setError('')
    if (window.isSecureContext === false || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Voice recording requires a supported browser and a secure connection.')
      return
    }
    const mimeType = recordingTypes.find(type => MediaRecorder.isTypeSupported(type))
    if (!mimeType) { setError('This browser does not support a recording format for voice input.'); return }
    const operation = ++operationRef.current
    const recordingLanguage = languageRef.current
    changePhase('requesting_permission')
    let stream: MediaStream
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }) }
    catch (reason) { fail(reason, operation); return }
    if (operationRef.current !== operation) { stream.getTracks().forEach(track => track.stop()); return }
    streamRef.current = stream
    stream.getTracks().forEach(track => { track.onended = () => { if (phaseRef.current === 'recording') fail(new Error('The microphone disconnected. Please try again.'), operation) } })
    try {
      const recorder = new MediaRecorder(stream, { mimeType })
      const chunks: Blob[] = []
      recorderRef.current = recorder
      recorder.ondataavailable = event => { if (operationRef.current === operation && event.data.size) chunks.push(event.data) }
      recorder.onerror = () => fail(new Error('The microphone stopped recording unexpectedly.'), operation)
      recorder.onstop = () => {
        if (operationRef.current !== operation) return
        recorderRef.current = null
        const clip = new Blob(chunks, { type: mimeType })
        if (!clip.size) { fail(new Error('No audio was recorded. Please try again.'), operation); return }
        if (clip.size > (configRef.current?.max_upload_bytes ?? 0)) {
          fail(new Error('The recording is too large. Please record a shorter question.'), operation)
          return
        }
        const request = new AbortController()
        requestRef.current = request
        void api.transcribe(clip, recordingLanguage, request.signal).then(result => {
          if (operationRef.current !== operation) return
          requestRef.current = null
          changePhase('idle')
          setElapsedSeconds(0)
          if (result.text.trim()) onTranscriptRef.current(result.text.trim())
          else setError('No speech was detected. Please try again.')
        }).catch(reason => { if (operationRef.current === operation) fail(reason, operation) })
      }
      recorder.start()
      changePhase('recording')
      setElapsedSeconds(0)
      const started = Date.now()
      intervalRef.current = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
      limitRef.current = window.setTimeout(stop, configRef.current!.max_duration_seconds * 1000)
    } catch (reason) { fail(reason, operation) }
  }

  useEffect(() => {
    const request = new AbortController()
    void loadConfigWithRetry(api.voiceConfig, request.signal).then(value => {
      if (request.signal.aborted) return
      configRef.current = value
      setConfig(value)
      if (value.languages.length && !value.languages.includes(languageRef.current)) {
        languageRef.current = value.languages[0]
        setLanguageState(value.languages[0])
      }
    }).catch(() => { /* Text chat remains available when voice is off or unreachable. */ })
    const onHidden = () => { if (document.visibilityState === 'hidden') cancel() }
    const onPageHide = () => cancel()
    document.addEventListener('visibilitychange', onHidden)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      request.abort()
      document.removeEventListener('visibilitychange', onHidden)
      window.removeEventListener('pagehide', onPageHide)
      cancel(false)
    }
    // Recorder resources are held in refs and are disposed on unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { enabled: Boolean(config?.enabled), languages: config?.languages || [], language, setLanguage, phase, active: phase !== 'idle', activeRef: phaseRef, elapsedSeconds, error, start, stop, cancel }
}
