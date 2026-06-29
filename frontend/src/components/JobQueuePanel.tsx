import { useEffect, useState } from 'react'

import { JOB_STARTED_EVENT } from '../api/client'
import { cancelAllJobs, cancelJob } from '../api/jobs'
import { useActiveJobs, useRecentJobs, useJobsStore } from '../store/jobsStore'
import type { Job } from '../types'

const STATUS_DOT: Record<string, string> = {
  RUNNING: 'bg-yellow-400',
  QUEUED: 'bg-slate-400',
  PENDING: 'bg-slate-400',
  COMPLETED: 'bg-green-400',
  FAILED: 'bg-red-400',
  CANCELLED: 'bg-slate-500',
}

function JobRow({
  job,
  onCancel,
  onSelect,
}: {
  job: Job
  onCancel?: (id: string) => void
  onSelect?: (job: Job) => void
}) {
  const isActive = job.status === 'RUNNING' || job.status === 'PENDING' || job.status === 'QUEUED'

  return (
    <button
      type="button"
      onClick={() => onSelect?.(job)}
      className="w-full border-t border-slate-700/60 px-3 py-2 text-left transition-colors first:border-t-0 hover:bg-slate-700/30"
    >
      <div className="flex items-center gap-2 min-w-0">
        {job.status === 'RUNNING' ? (
          <span className="h-2.5 w-2.5 flex-shrink-0 animate-spin rounded-full border-2 border-yellow-400 border-t-transparent" />
        ) : job.status === 'QUEUED' || job.status === 'PENDING' ? (
          <span className="h-2.5 w-2.5 flex-shrink-0 animate-spin rounded-full border-2 border-slate-400 border-t-transparent opacity-50" />
        ) : (
          <span className={`h-2 w-2 flex-shrink-0 rounded-full ${STATUS_DOT[job.status] ?? 'bg-slate-500'}`} />
        )}
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-200">
          {job.label ?? job.kind}
        </span>
        <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
          job.status === 'COMPLETED' ? 'bg-green-900 text-green-300' :
          job.status === 'FAILED' ? 'bg-red-900 text-red-300' :
          job.status === 'RUNNING' ? 'bg-yellow-900 text-yellow-300' :
          (job.status === 'QUEUED' || job.status === 'PENDING') ? 'bg-slate-700 text-slate-300' :
          'bg-slate-700 text-slate-400'
        }`}>{job.status}</span>
        {isActive && onCancel && (
          <button
            onClick={event => {
              event.stopPropagation()
              onCancel(job.job_id)
            }}
            className="flex-shrink-0 rounded bg-red-900/60 px-1.5 py-0.5 text-[10px] text-red-300 transition-colors hover:bg-red-800 hover:text-red-200"
            title="Cancel this job"
          >
            ✕
          </button>
        )}
      </div>
      {job.current_message && (
        <p className="mt-0.5 ml-4 truncate text-[11px] italic text-slate-400">
          {job.current_message}
        </p>
      )}
      {job.status === 'FAILED' && job.error && (
        <p className="mt-0.5 ml-4 truncate text-[11px] text-red-400">{job.error}</p>
      )}
    </button>
  )
}

function JobDetailPanel({
  job,
  onClose,
}: {
  job: Job | null
  onClose: () => void
}) {
  if (!job) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex flex-shrink-0 items-center gap-3 border-b border-slate-700 px-5 py-4">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold text-slate-100">
              {job.label ?? job.kind}
            </h3>
            <p className="mt-0.5 text-xs text-slate-400">
              {job.job_id} · {job.status}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-lg leading-none text-slate-400 hover:text-slate-200"
            aria-label="Close job details"
          >
            ×
          </button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,0.8fr)] overflow-hidden md:grid-cols-[1.2fr_0.8fr] md:grid-rows-1">
          <div className="min-h-0 overflow-y-auto border-b border-slate-700 md:border-b-0 md:border-r md:border-slate-700">
            <div className="px-5 py-4">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Event stream</h4>
              <div className="mt-3 space-y-2">
                {(job.events ?? []).length === 0 ? (
                  <p className="text-xs text-slate-500">No streamed events recorded.</p>
                ) : (
                  (job.events ?? []).map((event, index) => (
                    <div
                      key={`${event.timestamp ?? index}-${index}`}
                      className="rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2"
                    >
                      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-500">
                        <span>{event.stage ?? 'event'}</span>
                        {event.tool && <span>{event.tool}</span>}
                        {event.timestamp && (
                          <span className="ml-auto normal-case">{event.timestamp}</span>
                        )}
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-xs text-slate-200">
                        {event.message}
                      </p>
                      {event.payload !== undefined && (
                        <pre className="mt-2 max-h-60 overflow-auto rounded bg-slate-900 p-2 text-[11px] text-slate-400">
{JSON.stringify(event.payload, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="min-h-0 overflow-y-auto">
            <div className="space-y-4 px-5 py-4">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Current status</h4>
                <p className="mt-2 text-sm text-slate-200">{job.current_message ?? job.status}</p>
                {job.error && <p className="mt-2 text-xs text-red-400">{job.error}</p>}
              </div>

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Result</h4>
                {job.result ? (
                  <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-950 p-3 text-[11px] text-slate-300">
{JSON.stringify(job.result, null, 2)}
                  </pre>
                ) : (
                  <p className="mt-2 text-xs text-slate-500">No result payload.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function JobQueuePanel() {
  const [open, setOpen] = useState(false)
  const [confirmCancelAll, setConfirmCancelAll] = useState(false)
  const [cancellingAll, setCancellingAll] = useState(false)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)

  const activeJobs = useActiveJobs()
  const recentJobs = useRecentJobs(5)
  const selectedJob = useJobsStore(s => (selectedJobId ? s.jobs[selectedJobId] ?? null : null))

  const handleCancel = async (jobId: string) => {
    try {
      await cancelJob(jobId)
      useJobsStore.getState().patchJob(jobId, {
        status: 'CANCELLED',
        current_message: 'Cancelling…',
      })
    } catch {}
  }

  const handleCancelAll = async () => {
    setCancellingAll(true)
    try {
      await cancelAllJobs()
      const { jobs, patchJob } = useJobsStore.getState()
      for (const job of Object.values(jobs)) {
        if (job.status === 'RUNNING' || job.status === 'QUEUED' || job.status === 'PENDING') {
          patchJob(job.job_id, { status: 'CANCELLED', current_message: 'Cancelling…' })
        }
      }
    } catch {}
    finally {
      setCancellingAll(false)
      setConfirmCancelAll(false)
    }
  }

  useEffect(() => {
    if (activeJobs.length > 0) setOpen(true)
    else setConfirmCancelAll(false)
  }, [activeJobs.length])

  useEffect(() => {
    const onJobStarted = () => setOpen(true)
    window.addEventListener(JOB_STARTED_EVENT, onJobStarted)
    return () => window.removeEventListener(JOB_STARTED_EVENT, onJobStarted)
  }, [])

  const totalActive = activeJobs.length

  return (
    <div className="flex-shrink-0 border-t border-slate-700">
      <div className="flex w-full items-center gap-2 px-3 py-2.5 text-xs text-slate-400 transition-colors hover:bg-slate-700/50">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-slate-200"
        >
          <span className="text-sm leading-none">🔧</span>
          <span className="flex-1 text-left font-medium">Jobs</span>
          {totalActive > 0 && (
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-yellow-400" />
              <span className="font-semibold text-yellow-300">{totalActive}</span>
            </span>
          )}
        </button>
        {totalActive > 0 && (
          <button
            onClick={() => {
              setOpen(true)
              setConfirmCancelAll(true)
            }}
            disabled={cancellingAll}
            className="flex-shrink-0 rounded bg-red-900/60 px-2 py-0.5 text-[10px] text-red-300 transition-colors hover:bg-red-800 hover:text-red-200 disabled:opacity-50"
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

      {open && (
        <div className="max-h-72 overflow-y-auto bg-slate-800/50">
          {confirmCancelAll && (
            <div className="border-b border-slate-700/60 bg-red-950/40 px-3 py-2">
              <p className="text-xs font-medium text-red-200">
                Cancel all {totalActive} active job{totalActive === 1 ? '' : 's'}?
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">
                Running jobs will be interrupted; queued jobs will be discarded. This cannot be undone.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button
                  onClick={handleCancelAll}
                  disabled={cancellingAll || totalActive === 0}
                  className="rounded bg-red-700 px-2 py-1 text-[11px] font-medium text-white hover:bg-red-600 disabled:opacity-50"
                >
                  {cancellingAll ? 'Cancelling…' : `Yes, cancel ${totalActive}`}
                </button>
                <button
                  onClick={() => setConfirmCancelAll(false)}
                  disabled={cancellingAll}
                  className="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-600 disabled:opacity-50"
                >
                  Keep running
                </button>
              </div>
            </div>
          )}

          {activeJobs.length === 0 && recentJobs.length === 0 && (
            <p className="px-3 py-3 text-xs italic text-slate-500">No jobs.</p>
          )}

          {activeJobs.length > 0 && (
            <div>
              {activeJobs.map(job => (
                <JobRow
                  key={job.job_id}
                  job={job}
                  onCancel={handleCancel}
                  onSelect={selected => setSelectedJobId(selected.job_id)}
                />
              ))}
            </div>
          )}

          {recentJobs.length > 0 && (
            <div className={activeJobs.length > 0 ? 'opacity-60' : ''}>
              {activeJobs.length > 0 && (
                <p className="px-3 pb-0.5 pt-2 text-[10px] uppercase tracking-wide text-slate-500">
                  Recent
                </p>
              )}
              {recentJobs.map(job => (
                <JobRow
                  key={job.job_id}
                  job={job}
                  onSelect={selected => setSelectedJobId(selected.job_id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      <JobDetailPanel job={selectedJob} onClose={() => setSelectedJobId(null)} />
    </div>
  )
}
