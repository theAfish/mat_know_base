import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import type { Job } from '../types'

/**
 * Global jobs store. Owns the canonical client-side view of every job
 * the backend knows about (within the last poll window). Multiple
 * subscribers can react to job updates without each rolling their own
 * polling loop:
 *
 * - `JobQueuePanel` renders the active + recent slices.
 * - `ProjectDetail` / per-row badges read `useProjectActiveJobs(projectId)`
 *   to decide whether a project is currently processing.
 *
 * The store is *not* the only source of updates: imperative event-handler
 * polling (via `startJobPolling`) can also push updates here through
 * `upsertJob`, so the panel reflects per-action progress immediately,
 * without waiting for the next /api/jobs poll.
 */

const ACTIVE = new Set(['RUNNING', 'QUEUED', 'PENDING'])

interface JobsState {
  jobs: Record<string, Job>
  /** Replace the store contents (used by the global /api/jobs poll). */
  setJobs: (jobs: Job[]) => void
  /** Merge a single job. */
  upsertJob: (job: Job) => void
  /** Local-only mutation; the next poll will reconcile with the server. */
  patchJob: (jobId: string, patch: Partial<Job>) => void
}

function jobsEqual(a: Job, b: Job): boolean {
  return (
    a.status === b.status &&
    a.updated_at === b.updated_at &&
    a.current_message === b.current_message &&
    a.error === b.error &&
    a.label === b.label
  )
}

export const useJobsStore = create<JobsState>(set => ({
  jobs: {},
  setJobs: jobs => set(state => {
    const cur = state.jobs
    const next: Record<string, Job> = {}
    let changed = false
    for (const j of jobs) {
      const local = cur[j.job_id]
      // Preserve optimistic CANCELLED until the server confirms it.
      // Cancellation is cooperative — the worker may still report
      // RUNNING for a tick or two after the user clicked Cancel.
      if (local && local.status === 'CANCELLED' && ACTIVE.has(j.status)) {
        next[j.job_id] = local
        continue
      }
      if (local && jobsEqual(local, j)) {
        next[j.job_id] = local
      } else {
        next[j.job_id] = j
        changed = true
      }
    }
    if (!changed && Object.keys(cur).length === Object.keys(next).length) {
      return state
    }
    return { jobs: next }
  }),
  upsertJob: job => set(state => {
    const cur = state.jobs[job.job_id]
    if (cur && jobsEqual(cur, job)) return state
    return { jobs: { ...state.jobs, [job.job_id]: job } }
  }),
  patchJob: (jobId, patch) => set(state => {
    const cur = state.jobs[jobId]
    if (!cur) return state
    // Bump updated_at so the next setJobs() merge keeps treating the
    // optimistic patch as the freshest known state.
    return {
      jobs: {
        ...state.jobs,
        [jobId]: { ...cur, ...patch, updated_at: new Date().toISOString() },
      },
    }
  }),
}))

// ─── Selectors ────────────────────────────────────────────────────────────────
//
// `useShallow` keeps the array stable across polls when nothing actually
// changed — without it a fresh array (with new sort/filter output) was
// produced on every tick, forcing the panel to re-render needlessly.

export const useAllJobs = (): Job[] =>
  useJobsStore(useShallow(s =>
    Object.values(s.jobs).sort((a, b) => b.created_at.localeCompare(a.created_at)),
  ))

export const useActiveJobs = (): Job[] =>
  useJobsStore(useShallow(s =>
    Object.values(s.jobs)
      .filter(j => ACTIVE.has(j.status))
      .sort((a, b) => b.created_at.localeCompare(a.created_at)),
  ))

export const useRecentJobs = (limit = 5): Job[] =>
  useJobsStore(useShallow(s =>
    Object.values(s.jobs)
      .filter(j => !ACTIVE.has(j.status))
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .slice(0, limit),
  ))

export const useProjectActiveJobs = (projectId: string | null): Job[] =>
  useJobsStore(useShallow(s => projectId
    ? Object.values(s.jobs).filter(j => j.project_id === projectId && ACTIVE.has(j.status))
    : []))

export const isJobActive = (job: Job): boolean => ACTIVE.has(job.status)
