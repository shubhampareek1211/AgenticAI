const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

export function initialSession(): string | null {
  const linked = /^#session=([^&]+)$/.exec(window.location.hash)?.[1]
  return linked && UUID.test(linked) ? linked : null
}

export function signInReturnUrl(): string {
  const url = new URL('/auth/finish', window.location.origin)
  const session = initialSession()
  if (session) url.searchParams.set('session', session)
  return url.toString()
}

export function callbackSession(): string | null {
  const url = new URL(window.location.href)
  if (url.pathname !== '/auth/finish') return null
  const direct = url.searchParams.get('session')
  if (direct) return UUID.test(direct) ? direct : null
  const continueUrl = url.searchParams.get('continueUrl')
  if (!continueUrl) return null
  try {
    const continued = new URL(continueUrl)
    const nested = continued.searchParams.get('session')
    return continued.origin === url.origin && continued.pathname === '/auth/finish' && nested && UUID.test(nested) ? nested : null
  } catch { return null }
}

export function saveSession(id: string | null): void {
  const url = window.location.pathname + window.location.search + (id ? `#session=${encodeURIComponent(id)}` : '')
  window.history.replaceState(null, '', url)
}

export function sessionLink(id: string): string {
  return `${window.location.origin}${window.location.pathname}#session=${encodeURIComponent(id)}`
}
