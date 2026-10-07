import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type Transcription } from './api'
import { App } from './App'

vi.mock('./ChartCard', () => ({ ChartCard: () => <div>Chart</div> }))

class Recorder {
  static isTypeSupported = () => true
  state: RecordingState = 'inactive'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(_stream: MediaStream, readonly options: MediaRecorderOptions) {}
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio'], { type: this.options.mimeType }) } as BlobEvent)
    this.onstop?.()
  }
}

const result = (text: string): Transcription => ({ text, language: 'en', duration_seconds: 2, request_id: 'request' })
const originalRecorder = globalThis.MediaRecorder
const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
let stopTrack: ReturnType<typeof vi.fn>
let getUserMedia: ReturnType<typeof vi.fn>

beforeEach(() => {
  window.sessionStorage.clear()
  window.history.replaceState(null, '', '/')
  Element.prototype.scrollIntoView = vi.fn()
  window.requestAnimationFrame = callback => window.setTimeout(callback, 0)
  globalThis.MediaRecorder = Recorder as unknown as typeof MediaRecorder
  stopTrack = vi.fn()
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
  vi.spyOn(api, 'voiceConfig').mockResolvedValue({ enabled: true, max_duration_seconds: 60, max_upload_bytes: 8388608, languages: ['en'] })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  globalThis.MediaRecorder = originalRecorder
  if (originalMediaDevices) Object.defineProperty(navigator, 'mediaDevices', originalMediaDevices)
  else Reflect.deleteProperty(navigator, 'mediaDevices')
})

describe('voice composer', () => {
  it('keeps the draft, blocks chat during voice work, and sends only after editing', async () => {
    const transcribe = vi.spyOn(api, 'transcribe').mockResolvedValue(result('by season'))
    const allocate = vi.spyOn(api, 'allocate').mockResolvedValue('11111111-1111-4111-8111-111111111111')
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ response: 'Done', session_id: '11111111-1111-4111-8111-111111111111', tool_calls: [] })
    vi.spyOn(api, 'transcript').mockResolvedValue({ session_id: '11111111-1111-4111-8111-111111111111', request_state: 'idle', messages: [], tool_calls: [], charts: [] })
    render(<App />)
    const draft = screen.getByLabelText('Message Cricket Analyst') as HTMLTextAreaElement
    fireEvent.change(draft, { target: { value: 'Show runs' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Record a question' }))
    expect(await screen.findByRole('button', { name: 'Stop recording and transcribe' })).toBeTruthy()
    expect(draft.readOnly).toBe(true)
    expect(screen.getByRole('button', { name: 'Send message' }).hasAttribute('disabled')).toBe(true)
    fireEvent.keyDown(draft, { key: 'Enter', code: 'Enter' })
    fireEvent.submit(draft.closest('form')!)
    expect(chat).not.toHaveBeenCalled()
    expect(allocate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording and transcribe' }))
    await waitFor(() => expect(draft.value).toBe('Show runs by season'))
    expect(transcribe).toHaveBeenCalledTimes(1)
    expect(stopTrack).toHaveBeenCalled()
    expect(chat).not.toHaveBeenCalled()
    fireEvent.change(draft, { target: { value: 'Show runs by season in ODIs' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(chat).toHaveBeenCalledWith('11111111-1111-4111-8111-111111111111', 'Show runs by season in ODIs'))
    expect(allocate).toHaveBeenCalledTimes(1)
  })

  it('uses the selected Hindi language for a recording', async () => {
    vi.spyOn(api, 'voiceConfig').mockResolvedValue({ enabled: true, max_duration_seconds: 60, max_upload_bytes: 8388608, languages: ['en', 'hi', 'auto'] })
    const transcribe = vi.spyOn(api, 'transcribe').mockResolvedValue(result('विराट कोहली के रन दिखाओ'))
    render(<App />)
    const selector = await screen.findByLabelText('Recording language') as HTMLSelectElement
    fireEvent.change(selector, { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record a question' }))
    expect(await screen.findByRole('button', { name: 'Stop recording and transcribe' })).toBeTruthy()
    expect(selector.disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording and transcribe' }))
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1))
    expect(transcribe.mock.calls[0][1]).toBe('hi')
    expect((screen.getByLabelText('Message Cricket Analyst') as HTMLTextAreaElement).value).toBe('विराट कोहली के रन दिखाओ')
  })

  it('stops a microphone granted after the user cancels', async () => {
    let grant!: (stream: MediaStream) => void
    getUserMedia.mockImplementation(() => new Promise(resolve => { grant = resolve }))
    const transcribe = vi.spyOn(api, 'transcribe')
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Record a question' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    grant({ getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream)
    await waitFor(() => expect(stopTrack).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('button', { name: 'Stop recording and transcribe' })).toBeNull()
    expect(transcribe).not.toHaveBeenCalled()
  })

  it('discards a late transcription after cancellation', async () => {
    let finish!: (value: Transcription) => void
    let signal!: AbortSignal
    vi.spyOn(api, 'transcribe').mockImplementation((_file, _language, requestSignal) => { signal = requestSignal; return new Promise(resolve => { finish = resolve }) })
    render(<App />)
    const draft = screen.getByLabelText('Message Cricket Analyst') as HTMLTextAreaElement
    fireEvent.change(draft, { target: { value: 'Existing draft' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Record a question' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording and transcribe' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(signal.aborted).toBe(true)
    finish(result('Ignore this'))
    await waitFor(() => expect(draft.value).toBe('Existing draft'))
  })

  it('cancels recording when a new conversation starts', async () => {
    const transcribe = vi.spyOn(api, 'transcribe')
    render(<App />)
    const draft = screen.getByLabelText('Message Cricket Analyst') as HTMLTextAreaElement
    fireEvent.change(draft, { target: { value: 'Unsent draft' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Record a question' }))
    expect(await screen.findByRole('button', { name: 'Stop recording and transcribe' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(stopTrack).toHaveBeenCalled()
    expect(draft.value).toBe('')
    expect(screen.getByRole('button', { name: 'Record a question' })).toBeTruthy()
    expect(transcribe).not.toHaveBeenCalled()
  })

  it('keeps an overflowing transcript editable without truncating the draft', async () => {
    vi.spyOn(api, 'transcribe').mockResolvedValue(result('extra words'))
    render(<App />)
    const draft = screen.getByLabelText('Message Cricket Analyst') as HTMLTextAreaElement
    fireEvent.change(draft, { target: { value: 'a'.repeat(7995) } })
    fireEvent.click(await screen.findByRole('button', { name: 'Record a question' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording and transcribe' }))
    const overflow = await screen.findByLabelText(/transcript does not fit in your draft/) as HTMLTextAreaElement
    expect(draft.value).toHaveLength(7995)
    expect(overflow.value).toBe('extra words')
    fireEvent.change(draft, { target: { value: 'a'.repeat(7980) } })
    fireEvent.click(screen.getByRole('button', { name: 'Add to draft' }))
    expect(draft.value).toBe(`${'a'.repeat(7980)} extra words`)
    expect(screen.queryByLabelText(/transcript does not fit in your draft/)).toBeNull()
  })
})
