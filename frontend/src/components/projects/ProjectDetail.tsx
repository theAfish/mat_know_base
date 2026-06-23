import { useCallback, useEffect, useRef, useState } from 'react'

import { startJobPolling, type JobPollHandle } from '../../api/jobPolling'
import { cancelJob } from '../../api/jobs'
import {
  deleteProject,
  extractProject,
  getProject,
  getProjectJobs,
  kgExtractProject,
  processProject,
  projectToSpace,
  workflowExtractProject,
} from '../../api/projects'
import type { Job, Project, Space } from '../../types'
import JobProgress from '../JobProgress'
import StatusBadge from '../StatusBadge'
import ProjectAssetsPanel from './ProjectAssetsPanel'
import WorkflowGraphTab from './WorkflowGraphTab'
import GraphTab from '../frames/GraphTab'


export interface ProjectDetailProps {
  project: Project
  spaces: Space[]
  onClose: () => void
  onJobComplete?: () => void
  /** Called whenever the project record itself should be refreshed in the outer list. */
  onProjectUpdated?: (project: Project) => void
  onDeleted?: () => void
}

export default function ProjectDetail({
  project,
  spaces,
  onClose,
  onJobComplete,
  onProjectUpdated,
  onDeleted,
}: ProjectDetailProps) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [selectedSpace, setSelectedSpace] = useState<string>(spaces[0]?.space_id ?? '')
  const [selectedSourceType, setSelectedSourceType] = useState<'frame' | 'markdown'>('frame')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [graphView, setGraphView] = useState<'knowledge' | 'workflow'>('workflow')
  const pollHandleRef = useRef<JobPollHandle | null>(null)

  const userSpaces = spaces.filter(s => s.name !== '__global_kg__')

  const loadJobs = useCallback(async () => {
    try {
      const j = await getProjectJobs(project.project_id)
      setJobs(j)
    } catch { /* ignore */ }
  }, [project.project_id])

  useEffect(() => {
    loadJobs()
    return () => { pollHandleRef.current?.cancel() }
  }, [loadJobs])

  const refreshProject = useCallback(() => {
    if (!onProjectUpdated) return
    getProject(project.project_id).then(onProjectUpdated).catch(() => { /* ignore */ })
  }, [project.project_id, onProjectUpdated])

  const pollJob = useCallback((jobId: string) => {
    setActiveJobId(jobId)
    pollHandleRef.current?.cancel()
    pollHandleRef.current = startJobPolling({
      jobId,
      onUpdate: job => setJobs(prev => {
        const idx = prev.findIndex(j => j.job_id === jobId)
        if (idx === -1) return [job, ...prev]
        return prev.map(j => j.job_id === jobId ? job : j)
      }),
      // Refresh outer-list project tags incrementally so badges (Processed /
      // Frame / asset_count) progress while the job runs, not only at end.
      onTick: tick => { if (tick % 3 === 0) refreshProject() },
      onComplete: () => {
        setActiveJobId(null)
        loadJobs()
        refreshProject()
        onJobComplete?.()
      },
      onFailed: () => setActiveJobId(null),
    })
  }, [loadJobs, onJobComplete, refreshProject])

  const runAction = async (fn: () => Promise<{ job_id: string }>) => {
    try {
      setActionError(null)
      const { job_id } = await fn()
      pollJob(job_id)
    } catch (err: unknown) {
      console.error('Action failed', err)
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setActionError(e?.response?.data?.detail ?? e?.message ?? 'Action failed')
    }
  }

  const handleCancel = async () => {
    if (!activeJobId) return
    try {
      await cancelJob(activeJobId)
      setJobs(prev => prev.map(j =>
        j.job_id === activeJobId ? { ...j, status: 'CANCELLED' as const, current_message: 'Cancelling…' } : j
      ))
      setActiveJobId(null)
    } catch (err) {
      console.error('Cancel failed', err)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteProject(project.project_id)
      onDeleted?.()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setDeleteError(e?.response?.data?.detail ?? e?.message ?? 'Delete failed')
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const activeJob = jobs.find(j => j.job_id === activeJobId) ?? null
  const workflowActionLabel = project.workflow_status === 'IN_PROGRESS'
    ? '⛓ Resume Workflow'
    : project.workflow_status === 'FAILED'
      ? '⛓ Retry Workflow'
      : '⛓ Extract Workflow'

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-xl w-full max-w-5xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-slate-700">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-slate-100 truncate">
              {project.label ?? project.source_path ?? project.project_id.slice(0, 12)}
            </h3>
            <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-2 flex-wrap">
              <span>{project.asset_count} asset(s)</span>
              <span className="text-slate-600">·</span>
              <span>Processed: <StatusBadge status={project.processing_status ?? 'UNPROCESSED'} /></span>
              <span className="text-slate-600">·</span>
              <span>Frame: <StatusBadge status={project.frame_status ?? 'NO_FRAME'} /></span>
              <span className="text-slate-600">·</span>
              <span>Workflow: <StatusBadge status={project.workflow_status ?? 'NO_WORKFLOW'} /></span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 text-slate-400 hover:text-slate-200 text-xl leading-none"
          >×</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Action buttons */}
          {actionError && (
            <div className="rounded border border-red-700 bg-red-950/40 px-3 py-2 text-sm text-red-200">
              {actionError}
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => runAction(() => processProject(project.project_id))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              ⚙ Process files
            </button>
            <button
              onClick={() => runAction(() => extractProject(project.project_id, selectedSpace))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              🧪 Extract frame
            </button>
            <button
              onClick={() => runAction(() => projectToSpace(project.project_id, selectedSpace, selectedSourceType))}
              disabled={!!activeJobId || !selectedSpace}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              📊 Project to space
            </button>
            <button
              onClick={() => runAction(() => kgExtractProject(project.project_id))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              🕸 Extract graph
            </button>
            <button
              onClick={() => runAction(() => workflowExtractProject(project.project_id))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-violet-900/70 hover:bg-violet-800 disabled:opacity-40 rounded text-xs text-violet-100"
            >
              {workflowActionLabel}
            </button>
          </div>

          <div>
            <div className="flex gap-1 border-b border-slate-700 mb-3">
              {([['knowledge', 'Knowledge Graph'], ['workflow', 'Workflow Cards']] as const).map(([key, label]) => (
                <button key={key} onClick={() => setGraphView(key)}
                  className={`px-3 py-2 text-sm border-b-2 -mb-px ${graphView === key ? 'border-violet-400 text-violet-300' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                  {label}
                </button>
              ))}
            </div>
            {graphView === 'knowledge' && <GraphTab projectId={project.project_id} />}
            {graphView === 'workflow' && (
              <WorkflowGraphTab
                key={`${project.project_id}-${project.workflow_version ?? 0}`}
                projectId={project.project_id}
                actionsDisabled={!!activeJobId}
                onWorkflowVersionDeleted={refreshProject}
              />
            )}
          </div>

          {/* Space selector */}
          {userSpaces.length > 0 && (
            <div className="space-y-2">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Target space (for extract & project)</label>
                <select
                  value={selectedSpace}
                  onChange={e => setSelectedSpace(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                >
                  {userSpaces.map(s => (
                    <option key={s.space_id} value={s.space_id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Source for &ldquo;Project to space&rdquo;</label>
                <div className="flex gap-4">
                  {(['frame', 'markdown'] as const).map(src => (
                    <label key={src} className="flex items-center gap-1.5 cursor-pointer text-sm text-slate-300">
                      <input
                        type="radio"
                        name={`source-type-${project.project_id}`}
                        value={src}
                        checked={selectedSourceType === src}
                        onChange={() => setSelectedSourceType(src)}
                        className="accent-teal-500"
                      />
                      {src === 'frame' ? 'Frame' : 'Markdown'}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Active job progress */}
          {activeJob && (
            <div className="space-y-2">
              <JobProgress job={activeJob} />
              {(activeJob.status === 'RUNNING' || activeJob.status === 'QUEUED') && (
                <button
                  onClick={handleCancel}
                  className="text-xs px-3 py-1.5 rounded bg-red-900/60 hover:bg-red-800 text-red-300 hover:text-red-200 transition-colors"
                >
                  Cancel job
                </button>
              )}
            </div>
          )}

          {/* Asset list with per-asset processed upload */}
          <ProjectAssetsPanel projectId={project.project_id} />

          {/* Job history */}
          {jobs.length > 0 && (
            <div>
              <h4 className="text-xs text-slate-400 mb-2 uppercase tracking-wide">Recent jobs</h4>
              <div className="space-y-1">
                {jobs.slice(0, 8).map(job => (
                  <div
                    key={job.job_id}
                    className="flex items-center gap-2 px-3 py-1.5 bg-slate-700/50 rounded text-xs"
                  >
                    <StatusBadge status={job.status} />
                    <span className="text-slate-300 flex-1 truncate">{job.label ?? job.kind}</span>
                    <span className="text-slate-500">{job.created_at?.slice(0, 10)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Danger zone */}
          <div className="border-t border-slate-700 pt-4 mt-2">
            {!confirmDelete ? (
              <button
                onClick={() => setConfirmDelete(true)}
                disabled={!!activeJobId || deleting}
                className="px-3 py-1.5 text-xs bg-transparent hover:bg-red-900/40 text-red-400 hover:text-red-300 rounded border border-red-900 hover:border-red-700 disabled:opacity-40 transition-colors"
              >
                Delete this project…
              </button>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-red-300">
                  This will permanently delete the project, all its assets, processed files, frame, and projections. This cannot be undone.
                </p>
                {deleteError && (
                  <div className="px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-200 text-xs">
                    {deleteError}
                  </div>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="px-3 py-1.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded font-medium disabled:opacity-50"
                  >
                    {deleting ? 'Deleting…' : 'Yes, delete permanently'}
                  </button>
                  <button
                    onClick={() => { setConfirmDelete(false); setDeleteError(null) }}
                    disabled={deleting}
                    className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
