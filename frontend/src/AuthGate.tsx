import { useEffect, useRef, useState, type FormEvent } from 'react'
import { getApp, getApps, initializeApp, type FirebaseOptions } from 'firebase/app'
import {
  inMemoryPersistence,
  initializeAuth,
  isSignInWithEmailLink,
  onAuthStateChanged,
  sendSignInLinkToEmail,
  signInWithEmailLink,
  signOut,
  type Auth,
  type User,
} from 'firebase/auth'
import { App } from './App'
import { configureApiAuth } from './api'
import { callbackSession, saveSession, signInReturnUrl } from './session'

interface AuthConfig {
  enabled: boolean
  firebaseConfig: FirebaseOptions | null
}

export function isColumbiaEmail(email: string): boolean {
  return /^[^\s@]+@columbia\.edu$/i.test(email.trim())
}

function getPilotAuth(config: FirebaseOptions): Auth {
  const app = getApps().some(item => item.name === 'cricket-pilot')
    ? getApp('cricket-pilot')
    : initializeApp(config, 'cricket-pilot')
  return initializeAuth(app, { persistence: inMemoryPersistence })
}

function PilotGate({ firebaseConfig }: { firebaseConfig: FirebaseOptions }) {
  const [auth] = useState(() => getPilotAuth(firebaseConfig))
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)
  const [working, setWorking] = useState(false)
  const [onCallback, setOnCallback] = useState(window.location.pathname === '/auth/finish')
  const completionStarted = useRef(false)
  const hideConversation = () => { setUser(null); saveSession(null) }

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, next => {
      if (next && (!next.email || !next.emailVerified || !isColumbiaEmail(next.email))) {
        hideConversation()
        void signOut(auth).then(() => setError('Only verified @columbia.edu email addresses can use this pilot.'))
        return
      }
      setUser(next)
    }, () => setError('Could not check your sign-in. Reload the page and try again.'))
    configureApiAuth(async () => {
      const current = auth.currentUser
      if (!current) throw new Error('Your sign-in has ended. Sign in again.')
      try { return await current.getIdToken() }
      catch (reason) { hideConversation(); void signOut(auth); throw reason }
    }, () => {
      hideConversation()
      void signOut(auth).then(() => setError('Your sign-in has expired. Request a new link to continue.'))
    })
    return () => { unsubscribe(); configureApiAuth(null) }
  }, [auth])

  const completeLink = async (candidate: string) => {
    const address = candidate.trim()
    if (!isColumbiaEmail(address)) { setError('Enter your @columbia.edu email address.'); return }
    if (completionStarted.current) return
    completionStarted.current = true
    setWorking(true); setError('')
    const resume = callbackSession()
    try {
      const result = await signInWithEmailLink(auth, address, window.location.href)
      if (!result.user.emailVerified || !result.user.email || !isColumbiaEmail(result.user.email)) {
        await signOut(auth)
        throw new Error('Only verified @columbia.edu email addresses can use this pilot.')
      }
      window.history.replaceState(null, '', resume ? `/#session=${encodeURIComponent(resume)}` : '/')
      setOnCallback(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The sign-in link could not be used. Request a new one.')
      completionStarted.current = false
    } finally { setWorking(false) }
  }

  useEffect(() => {
    if (!onCallback) return
    if (!isSignInWithEmailLink(auth, window.location.href)) {
      setError('This sign-in link is invalid or expired. Request a new link.')
      return
    }
  }, [auth, onCallback])

  const sendLink = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const address = email.trim()
    if (!isColumbiaEmail(address)) { setError('Enter your @columbia.edu email address.'); return }
    setWorking(true); setError('')
    try {
      await sendSignInLinkToEmail(auth, address, {
        url: signInReturnUrl(),
        handleCodeInApp: true,
      })
      setSent(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not send a sign-in link. Try again.')
    } finally { setWorking(false) }
  }

  if (user === undefined && !onCallback) return <div className="auth-screen" role="status">Checking sign-in…</div>
  if (user && !onCallback) return <App key={`${firebaseConfig.projectId}:${user.uid}`} authEmail={user.email || ''} onSignOut={() => { hideConversation(); void signOut(auth) }} />

  return <div className="auth-screen"><section className="auth-card">
    <span className="eyebrow">CRICKET ANALYST PILOT</span>
    <h1>{onCallback ? 'Finish signing in' : 'Sign in with Columbia email'}</h1>
    <p>{onCallback ? 'Confirm the email address that received this link.' : 'We will email you a one-time sign-in link. You do not need a Google account.'}</p>
    {sent && <p role="status">Check {email.trim()} for your sign-in link. You can open it on this or another device.</p>}
    <form onSubmit={onCallback ? event => { event.preventDefault(); void completeLink(email) } : event => { void sendLink(event) }}>
      <label htmlFor="auth-email">Columbia email</label>
      <input id="auth-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="name@columbia.edu" required disabled={working} />
      <button type="submit" disabled={working}>{working ? 'Working…' : onCallback ? 'Finish sign-in' : 'Email me a link'}</button>
    </form>
    {error && <div className="banner-error" role="alert">{error}</div>}
    {onCallback && <button type="button" className="auth-link" onClick={() => { window.history.replaceState(null, '', '/'); setOnCallback(false); setError(''); completionStarted.current = false }}>Request a new link</button>}
  </section></div>
}

export function AuthGate() {
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetch('/auth/config').then(async response => {
      if (!response.ok) {
        if (response.status === 404 && ['localhost', '127.0.0.1'].includes(window.location.hostname)) return { enabled: false, firebaseConfig: null }
        throw new Error('Sign-in configuration is unavailable.')
      }
      return response.json() as Promise<AuthConfig>
    }).then(value => { if (active) setConfig(value) }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Sign-in is unavailable.') })
    return () => { active = false }
  }, [])

  if (error) return <div className="auth-screen" role="alert">{error} Please reload and try again.</div>
  if (!config) return <div className="auth-screen" role="status">Loading Cricket Analyst…</div>
  if (!config.enabled) { configureApiAuth(null); return <App /> }
  if (!config.firebaseConfig?.apiKey || !config.firebaseConfig.authDomain || !config.firebaseConfig.projectId || !config.firebaseConfig.appId) {
    return <div className="auth-screen" role="alert">Sign-in configuration is incomplete. Please contact the pilot administrator.</div>
  }
  return <PilotGate firebaseConfig={config.firebaseConfig} />
}
