import axios from 'axios'

const MAX_CONSECUTIVE_ERRORS = 6

/**
 * Returns true when polling should stop for this error.
 * - 404 means the job is gone (server restart or stale id).
 * - other 4xx are non-retryable client-side failures.
 * - network/proxy outages retry only for a bounded number of attempts.
 */
export function shouldStopJobPolling(error: unknown, consecutiveErrors: number): boolean {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    if (status === 404) return true
    if (status !== undefined && status >= 400 && status < 500) return true
  }

  return consecutiveErrors >= MAX_CONSECUTIVE_ERRORS
}

export function nextJobPollDelayMs(consecutiveErrors: number, baseMs = 1200, maxMs = 10000): number {
  const factor = Math.min(consecutiveErrors, 5)
  return Math.min(baseMs * Math.pow(2, factor), maxMs)
}
