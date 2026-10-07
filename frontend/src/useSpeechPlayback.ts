import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

const MAX_UTTERANCE_LENGTH = 220

function readableText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' Code example omitted. ')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[*_~]/g, '')
    .replace(/[>#|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function chunks(text: string): string[] {
  const parts: string[] = []
  let remaining = text
  while (remaining.length > MAX_UTTERANCE_LENGTH) {
    const window = remaining.slice(0, MAX_UTTERANCE_LENGTH)
    const sentence = Math.max(window.lastIndexOf('. '), window.lastIndexOf('? '), window.lastIndexOf('! '))
    const space = window.lastIndexOf(' ')
    const splitAt = sentence > MAX_UTTERANCE_LENGTH / 2 ? sentence + 1 : space > 0 ? space : MAX_UTTERANCE_LENGTH
    parts.push(remaining.slice(0, splitAt).trim())
    remaining = remaining.slice(splitAt).trimStart()
  }
  if (remaining) parts.push(remaining)
  return parts
}

async function* wavFrames(response: Response): AsyncGenerator<Blob> {
  const reader = response.body!.getReader()
  let buffer = new Uint8Array(0)
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) throw new Error('Generated speech stopped before it finished.')
      const joined = new Uint8Array(buffer.length + value.length)
      joined.set(buffer)
      joined.set(value, buffer.length)
      buffer = joined
      while (buffer.length >= 4) {
        const length = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0)
        if (length === 0) return
        if (length === 0xffffffff) throw new Error('Generated speech stopped before it finished.')
        if (length > 12 * 1024 * 1024) throw new Error('Generated speech returned an invalid audio chunk.')
        if (buffer.length < length + 4) break
        yield new Blob([buffer.slice(4, length + 4)], { type: 'audio/wav' })
        buffer = buffer.slice(length + 4)
      }
    }
  } finally { reader.releaseLock() }
}

export function useSpeechPlayback() {
  const browserSupported = typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'
  const [generatedAvailable, setGeneratedAvailable] = useState(false)
  const supported = browserSupported || generatedAvailable
  const [speakingId, setSpeakingId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [status, setStatus] = useState('')
  const generation = useRef(0)
  const request = useRef<AbortController | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const audioUrl = useRef<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void api.ttsConfig(controller.signal).then(config => setGeneratedAvailable(config.enabled && config.languages.includes('en'))).catch(() => setGeneratedAvailable(false))
    return () => controller.abort()
  }, [])

  const stop = useCallback(() => {
    generation.current += 1
    request.current?.abort()
    request.current = null
    audio.current?.pause()
    audio.current = null
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
    audioUrl.current = null
    if (browserSupported) window.speechSynthesis.cancel()
    setSpeakingId(null)
    setNotice('')
    setStatus('')
  }, [browserSupported])

  const speak = useCallback((id: string, markdown: string, sessionId?: string) => {
    if (!supported) return
    stop()
    const parts = chunks(readableText(markdown))
    if (!parts.length) return
    const current = generation.current
    setSpeakingId(id)
    setStatus('Preparing spoken answer…')
    const queuedAudio: Blob[] = []
    let streamFinished = false
    let playedAudio = false
    let incomplete = false
    let fallbackStarted = false
    const playBrowser = (index: number) => {
      if (generation.current !== current) return
      if (!browserSupported) { setSpeakingId(null); setStatus(''); return }
      if (index === parts.length) { setSpeakingId(null); setStatus(''); return }
      setStatus('Playing browser voice')
      const utterance = new window.SpeechSynthesisUtterance(parts[index])
      utterance.lang = /[\u0900-\u097f]/.test(parts[index]) ? 'hi-IN' : 'en-US'
      utterance.onend = () => playBrowser(index + 1)
      utterance.onerror = () => { if (generation.current === current) { setSpeakingId(null); setStatus(''); setNotice('The browser could not play this voice. Check your audio output and try again.') } }
      try { window.speechSynthesis.speak(utterance) }
      catch { if (generation.current === current) { setSpeakingId(null); setStatus(''); setNotice('The browser could not play this voice. Check your audio output and try again.') } }
    }
    const fallback = () => {
      if (generation.current !== current || fallbackStarted) return
      fallbackStarted = true
      audio.current?.pause()
      audio.current = null
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
      audioUrl.current = null
      setNotice('Kokoro could not generate this answer. Using your browser voice.')
      setStatus('Playing browser voice')
      playBrowser(0)
    }
    const playNext = () => {
      if (generation.current !== current || audio.current) return
      const next = queuedAudio.shift()
      if (!next) {
        if (streamFinished) { stop(); if (incomplete) setNotice('Generated speech stopped before the whole answer was read.') }
        else setStatus('Preparing next Kokoro segment…')
        return
      }
      const url = URL.createObjectURL(next)
      audioUrl.current = url
      const player = new Audio(url)
      audio.current = player
      player.onended = () => {
        if (generation.current !== current) return
        player.pause()
        audio.current = null
        URL.revokeObjectURL(url)
        audioUrl.current = null
        playNext()
      }
      player.onerror = () => { if (!playedAudio) fallback(); else { stop(); setNotice('Generated speech could not finish playing.') } }
      void player.play().then(() => { playedAudio = true; setStatus('Playing Kokoro voice') }).catch(() => { if (!playedAudio) fallback(); else { stop(); setNotice('Generated speech could not finish playing.') } })
    }
    // The pilot generates English only. Let the browser select its Hindi voice.
    if (!generatedAvailable || !sessionId || /[\u0900-\u097f]/.test(markdown)) { playBrowser(0); return }
    const controller = new AbortController()
    request.current = controller
    void (async () => {
      try {
        const response = await api.speech(sessionId, id, controller.signal)
        for await (const blob of wavFrames(response)) {
          if (generation.current !== current) return
          queuedAudio.push(blob)
          playNext()
        }
        streamFinished = true
        playNext()
      } catch {
        if (generation.current !== current) return
        if (!playedAudio) fallback()
        else { streamFinished = true; incomplete = true; playNext() }
      }
    })()
  }, [stop, supported, browserSupported, generatedAvailable])

  useEffect(() => () => {
    generation.current += 1
    request.current?.abort()
    audio.current?.pause()
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
    if (browserSupported) window.speechSynthesis.cancel()
  }, [browserSupported])

  return { supported, speakingId, notice, status, speak, stop }
}
