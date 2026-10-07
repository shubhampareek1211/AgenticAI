import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const firebase = vi.hoisted(() => {
  const state = { currentUser: null as null | { uid: string; email: string; emailVerified: boolean; getIdToken: () => Promise<string> } }
  let listener: (user: typeof state.currentUser) => void = () => {}
  return {
    state,
    setListener: (next: typeof listener) => { listener = next; next(state.currentUser) },
    notify: (user: typeof state.currentUser) => { state.currentUser = user; listener(user) },
    sendLink: vi.fn(async () => {}),
    complete: vi.fn(async () => ({ user: { uid: 'uid-1', email: 'student@columbia.edu', emailVerified: true, getIdToken: async () => 'token' } })),
    signOut: vi.fn(async () => {}),
    initializeAuth: vi.fn(() => ({ currentUser: state.currentUser })),
  }
})

vi.mock('firebase/app', () => ({ getApps: () => [], initializeApp: () => ({ name: 'cricket-pilot' }), getApp: () => ({ name: 'cricket-pilot' }) }))
vi.mock('firebase/auth', () => ({
  inMemoryPersistence: { type: 'NONE' },
  initializeAuth: firebase.initializeAuth,
  onAuthStateChanged: (_auth: unknown, callback: (user: typeof firebase.state.currentUser) => void) => { firebase.setListener(callback); return () => {} },
  isSignInWithEmailLink: () => true,
  sendSignInLinkToEmail: firebase.sendLink,
  signInWithEmailLink: firebase.complete,
  signOut: firebase.signOut,
}))
vi.mock('./App', () => ({ App: ({ authEmail, onSignOut }: { authEmail?: string; onSignOut?: () => void }) => <div>Conversation {authEmail}<button type="button" onClick={onSignOut}>Sign out</button></div> }))

import { AuthGate } from './AuthGate'

beforeEach(() => {
  const saved = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: vi.fn((key: string) => saved.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { saved.set(key, value) }),
    removeItem: vi.fn((key: string) => { saved.delete(key) }),
    clear: vi.fn(() => { saved.clear() }),
  } })
  window.history.replaceState(null, '', '/')
  firebase.state.currentUser = null
  firebase.initializeAuth.mockClear()
  firebase.sendLink.mockClear()
  firebase.complete.mockClear()
  firebase.signOut.mockClear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true, firebaseConfig: { apiKey: 'key', authDomain: 'example.firebaseapp.com', projectId: 'project', appId: 'app' } }) })))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Columbia email-link sign-in', () => {
  it('sends a link without storing the email and initializes memory-only auth', async () => {
    render(<AuthGate />)
    const input = await screen.findByLabelText('Columbia email')
    fireEvent.change(input, { target: { value: 'student@columbia.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Email me a link' }))
    await waitFor(() => expect(firebase.sendLink).toHaveBeenCalledWith(
      expect.anything(), 'student@columbia.edu', { url: `${window.location.origin}/auth/finish`, handleCodeInApp: true },
    ))
    expect(firebase.initializeAuth).toHaveBeenCalledWith(expect.anything(), { persistence: { type: 'NONE' } })
    expect(window.localStorage.setItem).not.toHaveBeenCalled()
    expect(window.localStorage.getItem('cricket_email_for_sign_in')).toBeNull()
  })

  it('prompts for the email on the callback before completing the link', async () => {
    window.history.replaceState(null, '', '/auth/finish?oobCode=one-use-code')
    render(<AuthGate />)
    const input = await screen.findByLabelText('Columbia email')
    expect(firebase.complete).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: 'student@columbia.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Finish sign-in' }))
    await waitFor(() => expect(firebase.complete).toHaveBeenCalledWith(expect.anything(), 'student@columbia.edu', expect.stringContaining('/auth/finish?oobCode=')))
    expect(window.location.pathname).toBe('/')
  })

  it('carries a conversation link through email sign-in without browser storage', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    window.history.replaceState(null, '', `/#session=${id}`)
    render(<AuthGate />)
    const input = await screen.findByLabelText('Columbia email')
    fireEvent.change(input, { target: { value: 'student@columbia.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Email me a link' }))
    await waitFor(() => expect(firebase.sendLink).toHaveBeenCalledWith(
      expect.anything(), 'student@columbia.edu', { url: `${window.location.origin}/auth/finish?session=${id}`, handleCodeInApp: true },
    ))
    cleanup()
    window.history.replaceState(null, '', `/auth/finish?session=${id}&oobCode=one-use-code`)
    render(<AuthGate />)
    const callbackEmail = await screen.findByLabelText('Columbia email')
    fireEvent.change(callbackEmail, { target: { value: 'student@columbia.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Finish sign-in' }))
    await waitFor(() => expect(window.location.hash).toBe(`#session=${id}`))
    expect(window.location.pathname).toBe('/')
    expect(window.location.search).toBe('')
    expect(window.localStorage.setItem).not.toHaveBeenCalled()
  })

  it('clears the conversation link on sign-out', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    window.history.replaceState(null, '', `/#session=${id}`)
    firebase.state.currentUser = { uid: 'uid-1', email: 'student@columbia.edu', emailVerified: true, getIdToken: async () => 'token' }
    render(<AuthGate />)
    await screen.findByText('Conversation student@columbia.edu')
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(window.location.hash).toBe('')
    expect(firebase.signOut).toHaveBeenCalledOnce()
  })

  it('rejects a non-Columbia address before requesting a link', async () => {
    render(<AuthGate />)
    const input = await screen.findByLabelText('Columbia email')
    fireEvent.change(input, { target: { value: 'student@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Email me a link' }))
    expect(await screen.findByText('Enter your @columbia.edu email address.')).toBeTruthy()
    expect(firebase.sendLink).not.toHaveBeenCalled()
  })
})
