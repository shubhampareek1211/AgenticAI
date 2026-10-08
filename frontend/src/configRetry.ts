import { ApiError } from './api'

const retryDelaysMs = [300, 900, 2700]

function transient(error: unknown): boolean {
  return (error instanceof ApiError && error.status >= 500 && error.status <= 599) || error instanceof TypeError
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return }
    const onAbort = () => { window.clearTimeout(timer); reject(signal.reason) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export async function loadConfigWithRetry<T>(load: (signal: AbortSignal) => Promise<T>, signal: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    if (signal.aborted) throw signal.reason
    try { return await load(signal) }
    catch (error) {
      if (signal.aborted || !transient(error) || attempt >= retryDelaysMs.length) throw error
      await pause(retryDelaysMs[attempt], signal)
    }
  }
}
