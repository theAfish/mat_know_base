import { useState, useEffect, useRef } from 'react'
import { listJobs } from '../api/jobs'
import type { Job } from '../types'

const STATUS_DOT: Record<string, string> = {
  RUNNING: 'bg-yellow-400',
  QUEUED: 'bg-slate-400',
  PENDING: 'bg-slate-400',
  COMPLETED: 'bg-green-400',
  FAILED: 'bg-red-400',
}

function JobRow({ job }: { job: Job }) {
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
          'bg-slate-700 text-slate-400'
        }`}>{job.status}</span>
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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const activeJobs = jobs.filter(j => j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING')
  const recentJobs = jobs.filter(j => j.status === 'COMPLETED' || j.status === 'FAILED').slice(0, 5)

  const fetchJobs = async () => {
    try {
      const data = await listJobs({ limit: 200 })
      setJobs(data)
      return data
    } catch {
      return jobs
    }
  }

  useEffect(() => {
    let cancelled = false

    const schedule = async () => {
      if (cancelled) return
      const data = await fetchJobs()
      if (cancelled) return
      const hasActive = data.some(j => j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING')
      timerRef.current = setTimeout(schedule, hasActive ? 1500 : 8000)
    }

    schedule()

    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auto-open when a job becomes active
  useEffect(() => {
    if (activeJobs.length > 0) setOpen(true)
  }, [activeJobs.length])

  const totalActive = activeJobs.length

  return (
    <div className="border-t border-slate-700 flex-shrink-0">
      {/* Header toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
      >
        <span className="text-sm leading-none">⚙️</span>
        <span className="flex-1 text-left font-medium">Jobs</span>
        {totalActive > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 bg-yellow-400 rounded-full animate-pulse" />
            <span className="text-yellow-300 font-semibold">{totalActive}</span>
          </span>
        )}
        <span className="text-slate-500">{open ? '▲' : '▼'}</span>
      </button>

      {/* Expandable job list */}
      {open && (
        <div className="max-h-72 overflow-y-auto bg-slate-800/50">
          {activeJobs.length === 0 && recentJobs.length === 0 && (
            <p className="px-3 py-3 text-xs text-slate-500 italic">No jobs.</p>
          )}

          {activeJobs.length > 0 && (
            <div>
              {activeJobs.map(job => <JobRow key={job.job_id} job={job} />)}
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
