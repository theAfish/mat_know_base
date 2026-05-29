import { useEffect, useRef } from 'react'
import { listJobs } from '../api/jobs'
import { nextJobPollDelayMs } from '../api/jobPolling'
import { JOB_STARTED_EVENT } from '../api/client'
import { useJobsStore, isJobActive } from '../store/jobsStore'

/**
 * Single global poll of /api/jobs. Mount exactly once near the app root.
 * Replaces the per-component polling that JobQueuePanel used to do.
 *
 * Cadence: 1.5s while any job is active, 8s otherwise. Errors back off
 * via `nextJobPollDelayMs`. A JOB_STARTED_EVENT (dispatched whenever an
 * API response carries a job_id) triggers an immediate refetch so newly
 * created jobs appear without waiting for the slow tick.
 */
export function useGlobalJobsPoller() {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const errorsRef = useRef(0)
  const fetchingRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    const fetchOnce = async () => {
      try {
        const data = await listJobs({ limit: 200 })
        errorsRef.current = 0
        useJobsStore.getState().setJobs(data)
        return data
      } catch {
        errorsRef.current += 1
        return null
      }
    }

    const schedule = async () => {
      if (cancelled || fetchingRef.current) return
      fetchingRef.current = true
      const data = await fetchOnce()
      fetchingRef.current = false
      if (cancelled) return
      if (errorsRef.current > 0) {
        timerRef.current = setTimeout(schedule, nextJobPollDelayMs(errorsRef.current, 2000, 15000))
        return
      }
      const hasActive = (data ?? []).some(isJobActive)
      timerRef.current = setTimeout(schedule, hasActive ? 1500 : 8000)
    }

    const reschedule = () => {
      if (fetchingRef.current) return
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
      schedule()
    }

    const onJobStarted = () => reschedule()
    window.addEventListener(JOB_STARTED_EVENT, onJobStarted)

    schedule()

    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
      window.removeEventListener(JOB_STARTED_EVENT, onJobStarted)
    }
  }, [])
}
