import axios from 'axios'
import { getJob } from './jobs'
import { useJobsStore } from '../store/jobsStore'
import type { Job } from '../types'

const MAX_CONSECUTIVE_ERRORS = 6

/** Statuses that mean the job is still running and we should keep polling. */
export const ACTIVE_JOB_STATUSES = new Set(['QUEUED', 'PENDING', 'RUNNING'])

/** Returns true when the job has reached a terminal state and polling should stop. */
export function isJobTerminal(status: string): boolean {
  return !ACTIVE_JOB_STATUSES.has(status)
}

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

export interface StartJobPollingOptions {
  /** Job id to poll. */
  jobId: string
  /** Called on every successful poll, including the final terminal one. */
  onUpdate?: (job: Job) => void
  /** Called once when the job reaches COMPLETED. */
  onComplete?: (job: Job) => void
  /**
   * Called once when the job reaches a terminal non-COMPLETED state
   * (FAILED, CANCELLED, …). Receives the final job, or null when
   * polling was abandoned due to repeated errors (e.g. 404 / network).
   */
  onFailed?: (job: Job | null, error?: unknown) => void
  /**
   * Called on every non-terminal successful tick, after onUpdate and
   * before the next poll is scheduled. `tick` is 1-indexed. Useful for
   * periodic side-effects (e.g. refetching related rows every N ticks).
   */
  onTick?: (tick: number, job: Job) => void
  /** Delay before the first poll. Default 500ms. */
  initialDelayMs?: number
  /** Delay between successful polls. Default 1000ms. */
  intervalMs?: number
}

export interface JobPollHandle {
  /** Stop polling. Safe to call multiple times. Suppresses further callbacks. */
  cancel(): void
}

/**
 * Imperative job poller. Spawns a timer chain that polls `getJob(jobId)`
 * until the job reaches a terminal state, errors out, or is cancelled.
 *
 * Consolidates the duplicated `pollJob` implementations that previously
 * lived in each page component. Use the `useJobPolling` hook for the
 * declarative single-job-in-an-effect case; use this helper for
 * event-handler-driven polling where you may have multiple jobs in flight.
 */
export function startJobPolling(opts: StartJobPollingOptions): JobPollHandle {
  const {
    jobId,
    onUpdate,
    onComplete,
    onFailed,
    onTick,
    initialDelayMs = 500,
    intervalMs = 1000,
  } = opts

  let cancelled = false
  let timer: ReturnType<typeof setTimeout> | null = null
  let consecutiveErrors = 0
  let tick = 0

  const cancel = () => {
    cancelled = true
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  const poll = async () => {
    if (cancelled) return
    try {
      const job = await getJob(jobId)
      if (cancelled) return
      consecutiveErrors = 0
      // Push every observed update into the global store so subscribers
      // (e.g. JobQueuePanel, per-project badges) reflect progress without
      // waiting for the next /api/jobs poll.
      useJobsStore.getState().upsertJob(job)
      onUpdate?.(job)
      if (isJobTerminal(job.status)) {
        if (job.status === 'COMPLETED') {
          onComplete?.(job)
        } else {
          onFailed?.(job)
        }
        return
      }
      tick += 1
      onTick?.(tick, job)
      timer = setTimeout(poll, intervalMs)
    } catch (error) {
      if (cancelled) return
      consecutiveErrors += 1
      if (shouldStopJobPolling(error, consecutiveErrors)) {
        onFailed?.(null, error)
        return
      }
      timer = setTimeout(poll, nextJobPollDelayMs(consecutiveErrors, intervalMs))
    }
  }

  timer = setTimeout(poll, initialDelayMs)
  return { cancel }
}
