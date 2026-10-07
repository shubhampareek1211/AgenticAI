import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, configureApiAuth } from './api'
import { callbackSession, initialSession, saveSession, sessionLink, signInReturnUrl } from './session'
import { isColumbiaEmail } from './AuthGate'

afterEach(() => {
  configureApiAuth(null)
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
})

describe('pilot API authentication', () => {
  it('attaches a fresh ID token to every protected API call, including voice and charts', async () => {
    const fetchMock = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => ({
      ok: true, status: 200,
      json: async () => path === '/sessions' ? { session_id: 's1' } : {},
    }))
    vi.stubGlobal('fetch', fetchMock)
    const getToken = vi.fn().mockResolvedValueOnce('first').mockResolvedValue('later')
    configureApiAuth(getToken)
    await api.voiceConfig()
    await api.transcribe(new Blob(['audio'], { type: 'audio/webm' }), 'hi', new AbortController().signal)
    await api.allocate()
    await api.transcript('s1')
    await api.chat('s1', 'hello')
    await api.chart('s1', 'c1')
    await api.recover('s1')
    await api.clear('s1')
    expect(fetchMock).toHaveBeenCalledTimes(8)
    expect(getToken).toHaveBeenCalledTimes(8)
    for (const [index, [, options]] of fetchMock.mock.calls.entries()) {
      const headers = (options as RequestInit).headers as Headers
      expect(headers.get('Authorization')).toBe(`Bearer ${index === 0 ? 'first' : 'later'}`)
    }
    expect(((fetchMock.mock.calls[1][1] as RequestInit).body as FormData).get('language')).toBe('hi')
  })

  it('reports a rejected token to the sign-in gate', async () => {
    const onUnauthorized = vi.fn()
    configureApiAuth(async () => 'bad-token', onUnauthorized)
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ detail: 'Expired token' }) })))
    await expect(api.voiceConfig()).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })
})

describe('conversation links', () => {
  it('keeps the session ID in the URL and does not use browser storage', () => {
    const id = '11111111-1111-4111-8111-111111111111'
    expect(initialSession()).toBeNull()
    saveSession(id)
    expect(window.location.hash).toBe(`#session=${id}`)
    expect(sessionLink(id)).toBe(`${window.location.origin}/#session=${id}`)
    expect(initialSession()).toBe(id)
    expect(window.sessionStorage.length).toBe(0)
    saveSession(null)
    expect(window.location.hash).toBe('')
    window.history.replaceState(null, '', '/#session=invalid')
    expect(initialSession()).toBeNull()
  })

  it('accepts only valid same-origin conversation IDs in sign-in callbacks', () => {
    const id = '11111111-1111-4111-8111-111111111111'
    saveSession(id)
    expect(signInReturnUrl()).toBe(`${window.location.origin}/auth/finish?session=${id}`)
    const continued = new URL(`/auth/finish?session=${id}`, window.location.origin)
    window.history.replaceState(null, '', `/auth/finish?continueUrl=${encodeURIComponent(continued.toString())}&oobCode=code`)
    expect(callbackSession()).toBe(id)
    window.history.replaceState(null, '', `/auth/finish?continueUrl=${encodeURIComponent(`https://elsewhere.example/auth/finish?session=${id}`)}`)
    expect(callbackSession()).toBeNull()
    window.history.replaceState(null, '', '/auth/finish?session=invalid')
    expect(callbackSession()).toBeNull()
  })

  it('accepts only the exact Columbia email domain', () => {
    expect(isColumbiaEmail('Student@Columbia.edu')).toBe(true)
    expect(isColumbiaEmail('a@sub.columbia.edu')).toBe(false)
    expect(isColumbiaEmail('a@columbia.edu.evil.test')).toBe(false)
    expect(isColumbiaEmail('a b@columbia.edu')).toBe(false)
  })
})
