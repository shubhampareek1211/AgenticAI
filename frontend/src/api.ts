export type ToolStatus = 'requested' | 'complete' | 'failed' | 'interrupted'

export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  result: string
  status: ToolStatus
  turn_number: number
  result_message_id: string
}

export interface TranscriptMessage {
  id: string
  ordinal: number
  turn_number: number
  role: 'system' | 'user' | 'assistant' | 'tool'
  payload: { content?: string; tool_call_id?: string; tool_calls?: unknown[] }
  created_at: string
}

export interface Transcript {
  session_id: string
  request_state: 'idle' | 'running' | 'interrupted'
  messages: TranscriptMessage[]
  tool_calls: ToolCall[]
  charts: string[]
}

export interface VoiceConfig {
  enabled: boolean
  max_duration_seconds: number
  max_upload_bytes: number
  languages: string[]
}

export interface TTSConfig {
  enabled: boolean
  languages: string[]
}

export interface Transcription {
  text: string
  language: string | null
  duration_seconds: number
  request_id: string
}

export interface ChartPoint {
  x: string | number | null
  y: number | null
  sample_size?: number
  wickets?: number
  [key: string]: unknown
}

export interface SavedChart {
  chart_id: string
  dataset_id: string
  schema_version: 2
  chart: {
    metric: string
    group_by: string
    chart_type: 'bar' | 'line' | 'scatter' | 'donut' | 'heatmap' | 'bubble' | 'stacked_area' | 'stacked_bar'
    title: string
    x_label: string
    y_label: string
    value_label?: string
    series: { name: string; points: ChartPoint[]; innings_number?: number }[]
  }
  coverage: Record<string, unknown>
  provenance: Record<string, unknown>[]
}

export interface ToolResult {
  ok: boolean
  data?: Record<string, unknown>
  error?: { code?: string; message?: string } | null
  coverage?: Record<string, unknown>
  provenance?: Record<string, unknown>[]
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

let tokenProvider: (() => Promise<string>) | null = null
let unauthorizedHandler: (() => void) | null = null

export function configureApiAuth(getToken: (() => Promise<string>) | null, onUnauthorized: (() => void) | null = null): void {
  tokenProvider = getToken
  unauthorizedHandler = onUnauthorized
}

async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  if (tokenProvider) headers.set('Authorization', `Bearer ${await tokenProvider()}`)
  const response = await fetch(path, { ...options, headers })
  if (response.status === 401) unauthorizedHandler?.()
  return response
}

async function json<T>(response: Response): Promise<T> {
  let body: Record<string, unknown>
  try { body = await response.json() as Record<string, unknown> }
  catch { throw new ApiError('The server returned an unreadable response.', response.status) }
  if (!response.ok) {
    throw new ApiError(typeof body.detail === 'string' ? body.detail : 'The request failed.', response.status)
  }
  return body as T
}

export const api = {
  voiceConfig: (signal?: AbortSignal) => request('/voice/config', { signal }).then(json<VoiceConfig>),
  ttsConfig: (signal?: AbortSignal) => request('/tts/config', { signal }).then(json<TTSConfig>),
  speech: async (sessionId: string, messageId: string, signal: AbortSignal): Promise<Response> => {
    const response = await request(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/speech`, { method: 'POST', signal })
    if (!response.ok) {
      let detail = 'Generated speech is unavailable.'
      try { const payload = await response.json() as { detail?: string }; detail = payload.detail || detail }
      catch { /* The browser voice remains available as a fallback. */ }
      throw new ApiError(detail, response.status)
    }
    if (!response.headers.get('Content-Type')?.startsWith('application/x-cricket-speech-stream') || !response.body) throw new ApiError('Generated speech returned an invalid format.', 502)
    return response
  },
  transcribe: (file: Blob, language: string, signal: AbortSignal) => {
    const data = new FormData()
    const extension = file.type.includes('mp4') ? 'm4a' : file.type.includes('ogg') ? 'ogg' : 'webm'
    data.append('file', file, `recording.${extension}`)
    data.append('language', language)
    return request('/transcribe', { method: 'POST', body: data, signal }).then(json<Transcription>)
  },
  allocate: async () => {
    const result = await json<{ session_id: string }>(await request('/sessions', { method: 'POST' }))
    return result.session_id
  },
  transcript: (id: string) => request(`/sessions/${encodeURIComponent(id)}`).then(json<Transcript>),
  chat: (id: string, message: string) => request('/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: id, message }),
  }).then(json<{ response: string; session_id: string; tool_calls: ToolCall[] }>),
  chart: (id: string, chartId: string) => request(`/sessions/${encodeURIComponent(id)}/charts/${encodeURIComponent(chartId)}`).then(json<SavedChart>),
  recover: (id: string) => request(`/sessions/${encodeURIComponent(id)}/recover`, { method: 'POST' }).then(json<{ status: string }>),
  clear: (id: string) => request(`/clear?session_id=${encodeURIComponent(id)}`, { method: 'POST' }).then(json<{ status: string }>),
}

export function parseToolResult(raw: string): ToolResult | null {
  try {
    const value: unknown = JSON.parse(raw)
    if (value && typeof value === 'object' && 'ok' in value) return value as ToolResult
  } catch { /* Progress can be observed before a result is committed. */ }
  return null
}
