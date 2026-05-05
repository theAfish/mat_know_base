import { useEffect, useRef, useCallback } from 'react'
import { getJob } from '../api/jobs'
import type { Job } from '../types'

interface Options {
  jobId: string | null
  interval?: number
  onComplete?: (job: Job) => void
  onFailed?: (job: Job) => void
  onProgress?: (job: Job) => void
}

export function useJobPolling({
  jobId,
  interval = 1000,
  onComplete,
  onFailed,
  onProgress,
}: Options) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeJobRef = useRef<string | null>(null)

  const onCompleteRef = useRef(onComplete)
  const onFailedRef = useRef(onFailed)
  const onProgressRef = useRef(onProgress)
  onCompleteRef.current = onComplete
  onFailedRef.current = onFailed
  onProgressRef.current = onProgress

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    activeJobRef.current = jobId
    stopPolling()

    if (!jobId) return

    const poll = async () => {
      if (activeJobRef.current !== jobId) return
      try {
        const job = await getJob(jobId)
        if (activeJobRef.current !== jobId) return

        if (job.status === 'COMPLETED') {
          onCompleteRef.current?.(job)
        } else if (job.status === 'FAILED') {
          onFailedRef.current?.(job)
        } else {
          onProgressRef.current?.(job)
          timerRef.current = setTimeout(poll, interval)
        }
      } catch {
        if (activeJobRef.current === jobId) {
          timerRef.current = setTimeout(poll, interval * 2)
        }
      }
    }

    timerRef.current = setTimeout(poll, interval)
    return stopPolling
  }, [jobId, interval, stopPolling])
}
