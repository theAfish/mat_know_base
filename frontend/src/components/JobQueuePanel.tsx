import { useState, useEffect, useRef } from 'react'
import { listJobs, cancelJob, cancelAllJobs } from '../api/jobs'
import { nextJobPollDelayMs } from '../api/jobPolling'
import { JOB_STARTED_EVENT } from '../api/client'
import type { Job } from '../types'

const STATUS_DOT: Record<string, string> = {
  RUNNING: 'bg-yellow-400',
  QUEUED: 'bg-slate-400',
  PENDING: 'bg-slate-400',
  COMPLETED: 'bg-green-400',
  FAILED: 'bg-red-400',
  CANCELLED: 'bg-slate-500',
}

function JobRow({ job, onCancel }: { job: Job; onCancel?: (id: string) => void }) {
  const isActive = job.status === 'RUNNING' || job.status === 'PENDING' || job.status === 'QUEUED'
  return (
    <div className="px-3 py-2 border-t border-slate-700/60 first:border-t-0">
      <div className="flex items-center gap-2 min-w-0">
        {job.status === 'RUNNING' ? (
          <span className="flex-shrink-0 w-2.5 h-2.5 border-2 border-yellow-400 border-t-transparent rounded-full animate-spin" />
        ) : (job.status === 'QUEUED' || job.status === 'PENDING') ? (
          <span className="flex-shrink-0 w-2.5 h-2.5 border-2 border-slate-400 border-t-transparent rounded-full animate-spin opacity-50" />
        ) : (
          <span className={`flex-shrink-0 w-2 h-2 rounded-full ${STATUS_DOT[job.status] ?? 'bg-slate-500'}`} />
        )}
        <span className="truncate text-xs text-slate-200 font-medium flex-1 min-w-0">
          {job.label ?? job.kind}
        </span>
        <span className={`flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium ${
          job.status === 'COMPLETED'                    ? 'bg-green-900 text-green-300' :
          job.status === 'FAILED'                       ? 'bg-red-900 text-red-300' :
          job.status === 'RUNNING'                      ? 'bg-yellow-900 text-yellow-300' :
          (job.status === 'QUEUED' || job.status === 'PENDING') ? 'bg-slate-700 text-slate-300' :
          job.status === 'CANCELLED'                    ? 'bg-slate-700 text-slate-400' :
          'bg-slate-700 text-slate-400'
        }`}>{job.status}</span>
        {isActive && onCancel && (
          <button
            onClick={() => onCancel(job.job_id)}
            className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-red-900/60 hover:bg-red-800 text-red-300 hover:text-red-200 transition-colors"
            title="Cancel this job"
          >
            ✕
          </button>
        )}
      </div>
      {isActive && job.current_message && (
        <p className="mt-0.5 ml-4 text-[11px] text-slate-400 truncate italic">
          {job.current_message}
        </p>
      )}
      {job.status === 'FAILED' && job.error && (
        <p className="mt-0.5 ml-4 text-[11px] text-red-400 truncate">{job.error}</p>
      )}
    </div>
  )
}

export default function JobQueuePanel() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState(false)
  const [confirmCancelAll, setConfirmCancelAll] = useState(false)
  const [cancellingAll, setCancellingAll] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const errorsRef = useRef(0)
  // fetchJobsRef lets the event listener trigger a poll without a stale closure
  const fetchJobsRef = useRef<() => void>(() => {})

  const activeJobs = jobs.filter(j => j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING')
  const recentJobs = jobs.filter(j => j.status === 'COMPLETED' || j.status === 'FAILED' || j.status === 'CANCELLED').slice(0, 5)

  const fetchJobs = async () => {
    try {
      const data = await listJobs({ limit: 200 })
      errorsRef.current = 0
      setJobs(data)
      return data
    } catch {
      errorsRef.current += 1
      return jobs
    }
  }

  const handleCancel = async (jobId: string) => {
    try {
      await cancelJob(jobId)
      // Optimistically update local state while the next poll confirms
      setJobs(prev => prev.map(j => j.job_id === jobId ? { ...j, status: 'CANCELLED' as const, current_message: 'Cancelling…' } : j))
    } catch { /* ignore — next poll will reflect the real state */ }
  }

  const handleCancelAll = async () => {
    setCancellingAll(true)
    try {
      await cancelAllJobs()
      setJobs(prev => prev.map(j =>
        (j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING')
          ? { ...j, status: 'CANCELLED' as const, current_message: 'Cancelling…' }
          : j
      ))
      // Force an immediate refresh so the UI reflects real backend state quickly
      fetchJobs()
    } catch { /* next poll reconciles */ }
    finally {
      setCancellingAll(false)
      setConfirmCancelAll(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    let fetching = false  // guard against concurrent schedule() chains

    const schedule = async () => {
      if (cancelled || fetching) return
      fetching = true
      const data = await fetchJobs()
      fetching = false
      if (cancelled) return
      if (errorsRef.current > 0) {
        timerRef.current = setTimeout(schedule, nextJobPollDelayMs(errorsRef.current, 2000, 15000))
        return
      }
      const hasActive = data.some(j => j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING')
      timerRef.current = setTimeout(schedule, hasActive ? 1500 : 8000)
    }

    const reschedule = () => {
      // If a fetch is already in-flight it will schedule the next one; skip.
      if (fetching) return
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
      schedule()
    }
    fetchJobsRef.current = reschedule

    const onJobStarted = () => { setOpen(true); reschedule() }
    window.addEventListener(JOB_STARTED_EVENT, onJobStarted)

    schedule()

    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
      window.removeEventListener(JOB_STARTED_EVENT, onJobStarted)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auto-open when a job becomes active
  useEffect(() => {
    if (activeJobs.length > 0) setOpen(true)
    else setConfirmCancelAll(false)
  }, [activeJobs.length])

  const totalActive = activeJobs.length

  return (
    <div className="border-t border-slate-700 flex-shrink-0">
      {/* Header toggle */}
      <div className="w-full flex items-center gap-2 px-3 py-2.5 text-xs text-slate-400 hover:bg-slate-700/50 transition-colors">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-2 flex-1 min-w-0 text-left hover:text-slate-200"
        >
          <span className="text-sm leading-none">🔧</span>
          <span className="flex-1 text-left font-medium">Jobs</span>
          {totalActive > 0 && (
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-yellow-400 rounded-full animate-pulse" />
              <span className="text-yellow-300 font-semibold">{totalActive}</span>
            </span>
          )}
        </button>
        {totalActive > 0 && (
          <button
            onClick={() => { setOpen(true); setConfirmCancelAll(true) }}
            disabled={cancellingAll}
            className="flex-shrink-0 text-[10px] px-2 py-0.5 rounded bg-red-900/60 hover:bg-red-800 text-red-300 hover:text-red-200 transition-colors disabled:opacity-50"
            title={`Cancel all ${totalActive} active job(s)`}
          >
            Cancel all
          </button>
        )}
        <button
          onClick={() => setOpen(o => !o)}
          className="flex-shrink-0 text-slate-500 hover:text-slate-300"
          aria-label={open ? 'Collapse jobs' : 'Expand jobs'}
        >
          {open ? '▲' : '▼'}
        </button>
      </div>

      {/* Expandable job list */}
      {open && (
        <div className="max-h-72 overflow-y-auto bg-slate-800/50">
          {confirmCancelAll && (
            <div className="px-3 py-2 border-b border-slate-700/60 bg-red-950/40">
              <p className="text-xs text-red-200 font-medium">
                Cancel all {totalActive} active job{totalActive === 1 ? '' : 's'}?
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">
                Running jobs will be interrupted; queued jobs will be discarded. This cannot be undone.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button
                  onClick={handleCancelAll}
                  disabled={cancellingAll || totalActive === 0}
                  className="text-[11px] px-2 py-1 rounded bg-red-700 hover:bg-red-600 text-white font-medium disabled:opacity-50"
                >
                  {cancellingAll ? 'Cancelling…' : `Yes, cancel ${totalActive}`}
                </button>
                <button
                  onClick={() => setConfirmCancelAll(false)}
                  disabled={cancellingAll}
                  className="text-[11px] px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 disabled:opacity-50"
                >
                  Keep running
                </button>
              </div>
            </div>
          )}
          {activeJobs.length === 0 && recentJobs.length === 0 && (
            <p className="px-3 py-3 text-xs text-slate-500 italic">No jobs.</p>
          )}

          {activeJobs.length > 0 && (
            <div>
              {activeJobs.map(job => <JobRow key={job.job_id} job={job} onCancel={handleCancel} />)}
            </div>
          )}

          {recentJobs.length > 0 && (
            <div className={activeJobs.length > 0 ? 'opacity-60' : ''}>
              {activeJobs.length > 0 && (
                <p className="px-3 pt-2 pb-0.5 text-[10px] uppercase tracking-wide text-slate-500">Recent</p>
              )}
              {recentJobs.map(job => <JobRow key={job.job_id} job={job} />)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
