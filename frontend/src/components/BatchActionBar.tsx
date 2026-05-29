import { useState, useEffect, useMemo } from 'react'
import { extractProject, projectToSpace, processProject } from '../api/projects'
import { getJob } from '../api/jobs'
import { nextJobPollDelayMs, shouldStopJobPolling, isJobTerminal } from '../api/jobPolling'
import type { Project, Space } from '../types'

// Matches StatusBadge colour palette so pills look consistent
const STATUS_PILL: Record<string, string> = {
  COMPLETED:   'bg-green-800/70 text-green-100 border-green-700/60',
  RUNNING:     'bg-yellow-800/70 text-yellow-100 border-yellow-700/60',
  IN_PROGRESS: 'bg-yellow-800/70 text-yellow-100 border-yellow-700/60',
  PENDING:     'bg-slate-700 text-slate-300 border-slate-600',
  FAILED:      'bg-red-800/70 text-red-100 border-red-700/60',
  NO_FRAME:    'bg-slate-700 text-slate-400 border-slate-600',
  REVIEWED:    'bg-teal-800/70 text-teal-100 border-teal-700/60',
  DRAFT:       'bg-blue-800/70 text-blue-100 border-blue-700/60',
}

interface RunStatus {
  total: number
  done: number
  failed: number
  running: boolean
  message: string
}

export default function BatchActionBar({
  selectedIds,
  allProjects,
  spaces,
  getStatus,
  onSelectionChange,
  onRefresh,
}: {
  selectedIds: Set<string>
  allProjects: Project[]
  spaces: Space[]
  getStatus: (id: string) => string
  onSelectionChange: (ids: Set<string>) => void
  onRefresh?: () => void
}) {
  const userSpaces = spaces.filter(s => s.name !== '__global_kg__')
  const [batchSpace, setBatchSpace] = useState(userSpaces[0]?.space_id ?? '')
  const [batchSourceType, setBatchSourceType] = useState<'frame' | 'markdown'>('frame')
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null)

  useEffect(() => {
    if (!batchSpace && userSpaces.length > 0) setBatchSpace(userSpaces[0].space_id)
  }, [userSpaces, batchSpace])

  // Count how many projects sit in each status — drives the quick-select pills
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const p of allProjects) {
      const s = getStatus(p.project_id)
      counts[s] = (counts[s] ?? 0) + 1
    }
    return counts
  }, [allProjects, getStatus])

  const runBatch = async (action: 'extract' | 'project' | 'process') => {
    const ids = Array.from(selectedIds)
    setRunStatus({ total: ids.length, done: 0, failed: 0, running: true, message: 'Starting jobs…' })

    // Phase 1: start all jobs, collecting job IDs
    const jobIds: string[] = []
    let startFailed = 0
    for (const id of ids) {
      try {
        let result: { job_id: string }
        if (action === 'extract') result = await extractProject(id, batchSpace || undefined)
        else if (action === 'process') result = await processProject(id)
        else result = await projectToSpace(id, batchSpace, batchSourceType)
        jobIds.push(result.job_id)
      } catch { startFailed++ }
      setRunStatus(s => s ? { ...s, done: jobIds.length, failed: startFailed } : null)
    }

    if (jobIds.length === 0) {
      setRunStatus({ total: ids.length, done: 0, failed: startFailed, running: false,
        message: `All ${startFailed} job(s) failed to start.` })
      return
    }

    setRunStatus(s => s ? { ...s, done: 0, failed: startFailed, message: `Waiting for ${jobIds.length} job(s)…` } : null)

    // Phase 2: poll all started jobs to actual completion
    let done = 0
    let failed = startFailed
    await Promise.all(jobIds.map(async (jobId) => {
      let delay = 1500
      let consecutiveErrors = 0
      while (true) {
        try {
          const job = await getJob(jobId)
          consecutiveErrors = 0
          if (job.status === 'COMPLETED') { done++; break }
          if (isJobTerminal(job.status)) { failed++; break }
          await new Promise(r => setTimeout(r, delay))
          delay = Math.min(delay * 1.5, 6000)
        } catch (error) {
          consecutiveErrors += 1
          if (shouldStopJobPolling(error, consecutiveErrors)) {
            failed++
            break
          }
          await new Promise(r => setTimeout(r, nextJobPollDelayMs(consecutiveErrors)))
        }
      }
      setRunStatus(s => s ? { ...s, done, failed } : null)
    }))

    setRunStatus({
      total: ids.length, done, failed, running: false,
      message: failed > 0 ? `${done} completed, ${failed} failed.` : `All ${done} completed.`,
    })
    onRefresh?.()
  }

  const count = selectedIds.size

  return (
    <div className="bg-slate-800 border border-teal-700/50 rounded-lg px-4 py-3 space-y-2.5">

      {/* Row 1: count, select-all/clear, status quick-select pills */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm text-teal-300 font-medium shrink-0">{count} selected</span>
        <button
          onClick={() => onSelectionChange(new Set(allProjects.map(p => p.project_id)))}
          className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
        >
          All {allProjects.length}
        </button>
        <button
          onClick={() => onSelectionChange(new Set())}
          className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
        >
          Clear
        </button>
        <span className="text-[11px] text-slate-500 mx-0.5">Select by status:</span>
        {Object.entries(statusCounts).map(([s, n]) => {
          const cls = STATUS_PILL[s] ?? 'bg-slate-700 text-slate-300 border-slate-600'
          return (
            <button
              key={s}
              onClick={() =>
                onSelectionChange(new Set(allProjects.filter(p => getStatus(p.project_id) === s).map(p => p.project_id)))
              }
              className={`text-[11px] px-2 py-0.5 rounded border ${cls} hover:opacity-75`}
              title={`Select all ${n} project(s) with status "${s}"`}
            >
              {s} ({n})
            </button>
          )
        })}
      </div>

      {/* Row 2: space + source type */}
      {userSpaces.length > 0 && (
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs text-slate-400">Space:</label>
          <select
            value={batchSpace}
            onChange={e => setBatchSpace(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-teal-500"
          >
            {userSpaces.map(s => <option key={s.space_id} value={s.space_id}>{s.name}</option>)}
          </select>
          <label className="text-xs text-slate-400 ml-2">Source:</label>
          {(['frame', 'markdown'] as const).map(src => (
            <label key={src} className="flex items-center gap-1 cursor-pointer text-xs text-slate-300">
              <input
                type="radio"
                value={src}
                checked={batchSourceType === src}
                onChange={() => setBatchSourceType(src)}
                className="accent-teal-500"
              />
              {src}
            </label>
          ))}
        </div>
      )}

      {/* Row 3: action buttons + live progress */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => runBatch('process')}
          disabled={runStatus?.running || count === 0}
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200"
        >
          📄 Batch Process ({count})
        </button>
        <button
          onClick={() => runBatch('extract')}
          disabled={runStatus?.running || count === 0}
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200"
        >
          🧪 Batch Extract ({count})
        </button>
        <button
          onClick={() => runBatch('project')}
          disabled={runStatus?.running || count === 0 || !batchSpace}
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200"
        >
          📊 Batch Project ({count})
        </button>
        {runStatus?.running && (
          <span className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin" />
            {runStatus.done + runStatus.failed}/{runStatus.total}
            {runStatus.failed > 0 && <span className="text-red-400"> ({runStatus.failed} failed)</span>}
          </span>
        )}
        {!runStatus?.running && runStatus?.message && (
          <span className={`text-xs ${runStatus.failed > 0 ? 'text-amber-300' : 'text-green-300'}`}>
            ✓ {runStatus.message}
          </span>
        )}
      </div>

    </div>
  )
}
