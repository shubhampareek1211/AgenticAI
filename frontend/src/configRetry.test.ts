import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { loadConfigWithRetry } from './configRetry'

afterEach(() => vi.useRealTimers())

describe('voice configuration retry', () => {
  it('recovers from a transient server error', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockRejectedValueOnce(new ApiError('warming up', 503)).mockResolvedValue({ enabled: true })
    const pending = loadConfigWithRetry(load, new AbortController().signal)
    await vi.advanceTimersByTimeAsync(300)
    await expect(pending).resolves.toEqual({ enabled: true })
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('retries network errors only a bounded number of times', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockRejectedValue(new TypeError('network unavailable'))
    const pending = loadConfigWithRetry(load, new AbortController().signal)
    const result = expect(pending).rejects.toThrow('network unavailable')
    await vi.runAllTimersAsync()
    await result
    expect(load).toHaveBeenCalledTimes(4)
  })

  it('does not retry authentication or other client errors', async () => {
    vi.useFakeTimers()
    for (const status of [401, 403, 404, 429]) {
      const load = vi.fn().mockRejectedValue(new ApiError('rejected', status))
      await expect(loadConfigWithRetry(load, new AbortController().signal)).rejects.toMatchObject({ status })
      expect(load).toHaveBeenCalledTimes(1)
    }
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels the pending retry when its component unmounts', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const load = vi.fn().mockRejectedValue(new ApiError('warming up', 503))
    const pending = loadConfigWithRetry(load, controller.signal)
    await Promise.resolve()
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    await vi.runAllTimersAsync()
    expect(load).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})
