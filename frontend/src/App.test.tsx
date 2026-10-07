import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, ApiError, type Transcript } from './api'
import { App } from './App'

vi.mock('./ChartCard', () => ({ ChartCard: ({ chartId }: { chartId: string }) => <div>Saved chart {chartId}</div> }))

const id = '11111111-1111-4111-8111-111111111111'
const message = (ordinal: number, role: 'user' | 'assistant' | 'tool', content: string, extra = {}) => ({ id: `m${ordinal}`, ordinal, turn_number: 1, role, payload: { content, ...extra }, created_at: '2025-01-01T00:00:00Z' })
const transcript = (state: Transcript['request_state'], messages: Transcript['messages'] = [], tool_calls: Transcript['tool_calls'] = []): Transcript => ({ session_id: id, request_state: state, messages, tool_calls, charts: [] })
const speechResponse = (...parts: string[]): Response => {
  const frames = parts.flatMap(part => {
    const bytes = new TextEncoder().encode(part)
    return [0, 0, 0, bytes.length, ...bytes]
  })
  return new Response(new Uint8Array([...frames, 0, 0, 0, 0]), { headers: { 'Content-Type': 'application/x-cricket-speech-stream' } })
}

beforeEach(() => { window.history.replaceState(null, '', '/'); Element.prototype.scrollIntoView = vi.fn() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('conversation', () => {
  it('restores persisted messages and tool progress by ID', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('running', [
      message(1, 'user', 'Plot runs'),
      message(2, 'tool', '{}', { tool_call_id: 'c1' }),
    ], [{ id: 'c1', name: 'create_cricket_chart', args: {}, result: '{}', status: 'requested', turn_number: 1, result_message_id: 'm2' }]))
    render(<App />)
    expect(await screen.findByText('Plot runs')).toBeTruthy()
    expect(screen.getByText('create cricket chart')).toBeTruthy()
    expect(screen.getByText('running')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Send message' }).hasAttribute('disabled')).toBe(true)
  })

  it('allocates before chat and reconciles the saved answer once', async () => {
    const order: string[] = []
    vi.spyOn(api, 'allocate').mockImplementation(async () => { order.push('allocate'); return id })
    vi.spyOn(api, 'chat').mockImplementation(async () => { order.push('chat'); return { response: 'Here is the answer.', session_id: id, tool_calls: [] } })
    vi.spyOn(api, 'transcript').mockImplementation(async () => transcript('idle', [message(1, 'user', 'Plot runs'), message(2, 'assistant', 'Here is the answer.')]))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Message Cricket Analyst'), { target: { value: 'Plot runs' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(screen.getByText('Here is the answer.')).toBeTruthy())
    expect(order).toEqual(['allocate', 'chat'])
    expect(screen.getAllByText('Plot runs')).toHaveLength(1)
    expect(window.location.hash).toBe(`#session=${id}`)
  })

  it('reads answers on request and auto-reads only a newly completed answer', async () => {
    const speak = vi.fn()
    const cancel = vi.fn()
    class Utterance { constructor(public text: string) {} }
    vi.stubGlobal('SpeechSynthesisUtterance', Utterance)
    vi.stubGlobal('speechSynthesis', { speak, cancel })
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript')
      .mockResolvedValueOnce(transcript('idle', [message(1, 'assistant', 'Saved answer')]))
      .mockResolvedValue(transcript('idle', [message(1, 'assistant', 'Saved answer'), message(2, 'user', 'New question'), message(3, 'assistant', '**New answer**.')]))
    vi.spyOn(api, 'chat').mockResolvedValue({ response: 'New answer.', session_id: id, tool_calls: [] })
    render(<App />)
    expect(await screen.findByText('Saved answer')).toBeTruthy()
    expect(speak).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Read answer aloud' }))
    expect(speak).toHaveBeenCalledTimes(1)
    expect(speak.mock.calls[0][0].text).toBe('Saved answer')
    fireEvent.click(screen.getByRole('button', { name: 'Stop speaking' }))
    expect(cancel).toHaveBeenCalled()
    fireEvent.click(screen.getByLabelText('Read answers aloud'))
    fireEvent.change(screen.getByLabelText('Message Cricket Analyst'), { target: { value: 'New question' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(speak).toHaveBeenCalledTimes(2))
    expect(speak.mock.calls[1][0].text).toBe('New answer.')
  })

  it('uses saved-answer Kokoro audio when available and stops it on request', async () => {
    const browserSpeak = vi.fn()
    vi.stubGlobal('SpeechSynthesisUtterance', class { constructor(public text: string) {} })
    vi.stubGlobal('speechSynthesis', { speak: browserSpeak, cancel: vi.fn() })
    const play = vi.fn().mockResolvedValue(undefined)
    const pause = vi.fn()
    vi.stubGlobal('Audio', class { onended: (() => void) | null = null; onerror: (() => void) | null = null; play = play; pause = pause })
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:answer'), revokeObjectURL: vi.fn() })
    vi.spyOn(api, 'ttsConfig').mockResolvedValue({ enabled: true, languages: ['en'] })
    const speech = vi.spyOn(api, 'speech').mockResolvedValue(speechResponse('RIFF'))
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('idle', [message(1, 'assistant', 'Kohli scored 82 runs.')]))
    render(<App />)
    expect(await screen.findByText('Kohli scored 82 runs.')).toBeTruthy()
    await waitFor(() => expect(api.ttsConfig).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Read answer aloud' }))
    await waitFor(() => expect(speech).toHaveBeenCalledWith(id, 'm1', expect.any(AbortSignal)))
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1))
    expect(browserSpeak).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Stop speaking' }))
    expect(pause).toHaveBeenCalled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:answer')
  })

  it('plays the first Kokoro segment before the rest of a long answer arrives', async () => {
    const players: Array<{ onended: (() => void) | null; play: ReturnType<typeof vi.fn> }> = []
    vi.stubGlobal('Audio', class {
      onended: (() => void) | null = null
      onerror: (() => void) | null = null
      play = vi.fn().mockResolvedValue(undefined)
      pause = vi.fn()
      constructor() { players.push(this) }
    })
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => `blob:part-${players.length}`), revokeObjectURL: vi.fn() })
    vi.spyOn(api, 'ttsConfig').mockResolvedValue({ enabled: true, languages: ['en'] })
    let send!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(controller) { send = controller } })
    vi.spyOn(api, 'speech').mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'application/x-cricket-speech-stream' } }))
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('idle', [message(1, 'assistant', 'A long answer.')]))
    render(<App />)
    await screen.findByText('A long answer.')
    await waitFor(() => expect(api.ttsConfig).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Read answer aloud' }))
    send.enqueue(new Uint8Array([0, 0, 0, 4, ...new TextEncoder().encode('RIFF')]))
    await waitFor(() => expect(players[0]?.play).toHaveBeenCalledTimes(1))
    expect(screen.getByText('Playing Kokoro voice')).toBeTruthy()
    send.enqueue(new Uint8Array([0, 0, 0, 4, ...new TextEncoder().encode('WAVE'), 0, 0, 0, 0]))
    players[0].onended?.()
    await waitFor(() => expect(players[1]?.play).toHaveBeenCalledTimes(1))
  })

  it('falls back to browser speech if generated audio fails', async () => {
    const browserSpeak = vi.fn()
    vi.stubGlobal('SpeechSynthesisUtterance', class { constructor(public text: string) {} })
    vi.stubGlobal('speechSynthesis', { speak: browserSpeak, cancel: vi.fn() })
    vi.spyOn(api, 'ttsConfig').mockResolvedValue({ enabled: true, languages: ['en'] })
    vi.spyOn(api, 'speech').mockRejectedValue(new ApiError('Worker unavailable', 503))
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('idle', [message(1, 'assistant', 'Saved answer')]))
    render(<App />)
    expect(await screen.findByText('Saved answer')).toBeTruthy()
    await waitFor(() => expect(api.ttsConfig).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Read answer aloud' }))
    await waitFor(() => expect(browserSpeak).toHaveBeenCalledTimes(1))
    expect(browserSpeak.mock.calls[0][0].text).toBe('Saved answer')
  })

  it('recovers interrupted work before presenting the saved transcript', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    const get = vi.spyOn(api, 'transcript')
      .mockResolvedValueOnce(transcript('interrupted', [message(1, 'user', 'Question')]))
      .mockResolvedValue(transcript('idle', [message(1, 'user', 'Question'), message(2, 'assistant', 'Previous request was interrupted.')]))
    const recover = vi.spyOn(api, 'recover').mockResolvedValue({ status: 'ok' })
    render(<App />)
    expect(await screen.findByText('Previous request was interrupted.')).toBeTruthy()
    expect(recover).toHaveBeenCalledWith(id)
    expect(get.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('locks conversation actions until deletion settles', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('idle', [message(1, 'assistant', 'Saved answer')]))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let finish!: (value: { status: string }) => void
    vi.spyOn(api, 'clear').mockImplementation(() => new Promise(resolve => { finish = resolve }))
    render(<App />)
    expect(await screen.findByText('Saved answer')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByRole('button', { name: 'New conversation' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Clear' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Send message' }).hasAttribute('disabled')).toBe(true)
    finish({ status: 'ok' })
    await waitFor(() => expect(window.location.hash).toBe(''))
  })

  it('blocks another send after an uncertain network failure', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    let unavailable = false
    vi.spyOn(api, 'transcript').mockImplementation(async () => {
      if (unavailable) throw new Error('network offline')
      return transcript('idle', [message(1, 'assistant', 'Saved answer')])
    })
    const chat = vi.spyOn(api, 'chat').mockImplementation(async () => { unavailable = true; throw new Error('network offline') })
    render(<App />)
    expect(await screen.findByText('Saved answer')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Message Cricket Analyst'), { target: { value: 'Plot runs' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText(/Waiting for saved progress before another request/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'New conversation' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(chat).toHaveBeenCalledTimes(1)
  })

  it('keeps an interrupted conversation locked when recovery fails', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockResolvedValue(transcript('interrupted', [message(1, 'user', 'Pending')]))
    const recover = vi.spyOn(api, 'recover').mockRejectedValue(new ApiError('Storage unavailable', 503))
    render(<App />)
    expect(await screen.findByText(/Could not load saved progress: Storage unavailable/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'New conversation' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Send message' }).hasAttribute('disabled')).toBe(true)
    expect(recover).toHaveBeenCalledTimes(1)
  })

  it('drops a linked conversation that belongs to another account', async () => {
    window.history.replaceState(null, '', `/#session=${id}`)
    vi.spyOn(api, 'transcript').mockRejectedValue(new ApiError('Forbidden', 403))
    render(<App />)
    expect(await screen.findByText(/This conversation is unavailable to your account/)).toBeTruthy()
    expect(window.location.hash).toBe('')
    expect(screen.getByRole('button', { name: 'New conversation' }).hasAttribute('disabled')).toBe(false)
  })
})
