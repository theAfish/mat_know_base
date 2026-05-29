import { useEffect, useRef } from 'react'
import { startJobPolling } from '../api/jobPolling'
import type { Job } from '../types'

interface Options {
  jobId: string | null
  interval?: number
  onComplete?: (job: Job) => void
  onFailed?: (job: Job | null) => void
  onProgress?: (job: Job) => void
}

/**
 * Declarative wrapper around `startJobPolling` that ties polling lifetime
 * to a React effect. Use this when a component owns at most one active
 * job at a time and the job id flows in from props/state. For imperative
 * polling driven by event handlers (possibly many jobs in flight), call
 * `startJobPolling` directly.
 */
export function useJobPolling({
  jobId,
  interval = 1000,
  onComplete,
  onFailed,
  onProgress,
}: Options) {
  const onCompleteRef = useRef(onComplete)
  const onFailedRef = useRef(onFailed)
  const onProgressRef = useRef(onProgress)
  onCompleteRef.current = onComplete
  onFailedRef.current = onFailed
  onProgressRef.current = onProgress

  useEffect(() => {
    if (!jobId) return
    const handle = startJobPolling({
      jobId,
      intervalMs: interval,
      onUpdate: job => onProgressRef.current?.(job),
      onComplete: job => onCompleteRef.current?.(job),
      onFailed: job => onFailedRef.current?.(job),
    })
    return handle.cancel
  }, [jobId, interval])
}

