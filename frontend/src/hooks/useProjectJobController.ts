import { useCallback, useEffect, useState } from 'react'

import { JOB_FINISHED_EVENT } from '../api/jobPolling'
import { cancelJob } from '../api/jobs'
import { useJobsStore } from '../store/jobsStore'
import type { Job } from '../types'

type StartJob = () => Promise<{ job_id: string }>

interface Options {
  onComplete?: (job: Job) => void
  onSettled?: (job: Job) => void
}

/**
 * Coordinates a project detail's currently-started action while the app-level
 * jobs poller remains the only network polling layer.
 */
export function useProjectJobController({ onComplete, onSettled }: Options = {}) {
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const activeJob = useJobsStore(s => activeJobId ? s.jobs[activeJobId] ?? null : null)

  useEffect(() => {
    const handleFinished = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (job.job_id !== activeJobId) return
      setActiveJobId(null)
      if (job.status === 'COMPLETED') onComplete?.(job)
      onSettled?.(job)
    }
    window.addEventListener(JOB_FINISHED_EVENT, handleFinished)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, handleFinished)
  }, [activeJobId, onComplete, onSettled])

  const run = useCallback(async (start: StartJob) => {
    if (activeJobId) return null
    try {
      setActionError(null)
      const result = await start()
      setActiveJobId(result.job_id)
      return result.job_id
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } }; message?: string }
      setActionError(err.response?.data?.detail ?? err.message ?? 'Action failed')
      return null
    }
  }, [activeJobId])

  const cancel = useCallback(async () => {
    if (!activeJobId) return
    try {
      await cancelJob(activeJobId)
      useJobsStore.getState().patchJob(activeJobId, {
        status: 'CANCELLED',
        current_message: 'Cancelling…',
      })
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } }; message?: string }
      setActionError(err.response?.data?.detail ?? err.message ?? 'Cancel failed')
    }
  }, [activeJobId])

  return { activeJobId, activeJob, actionError, setActionError, run, cancel }
}
