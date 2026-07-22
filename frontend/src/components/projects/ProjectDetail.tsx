import { lazy, Suspense, useState } from 'react'

import {
  deleteProject,
  extractProject,
  kgExtractProject,
  processProject,
  projectToSpace,
  workflowExtractProject,
} from '../../api/projects'
import { useProjectRefresh } from '../../hooks/useProjectRefresh'
import { useProjectJobController } from '../../hooks/useProjectJobController'
import { useProjectJobs } from '../../store/jobsStore'
import type { Project, Space } from '../../types'
import JobProgress from '../JobProgress'
import StatusBadge from '../StatusBadge'
import ProjectAssetsPanel from './ProjectAssetsPanel'
import { ProjectDetailHeader, ProjectDetailTabs } from './ProjectDetailChrome'

const WorkflowGraphTab = lazy(() => import('./WorkflowGraphTab'))
const GraphTab = lazy(() => import('../frames/GraphTab'))


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
  const [selectedSpace, setSelectedSpace] = useState<string>(spaces[0]?.space_id ?? '')
  const [selectedSourceType, setSelectedSourceType] = useState<'frame' | 'markdown'>('frame')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [graphView, setGraphView] = useState<'knowledge' | 'workflow'>('workflow')

  const userSpaces = spaces.filter(s => s.name !== '__global_kg__')

  const refreshProject = useProjectRefresh(project.project_id, onProjectUpdated)
  const jobs = useProjectJobs(project.project_id)
  const { activeJobId, activeJob, actionError, run: runAction, cancel: handleCancel } = useProjectJobController({
    onComplete: () => {
      refreshProject()
      onJobComplete?.()
    },
  })

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
          <ProjectDetailHeader project={project} trailing={
            <button onClick={onClose} className="flex-shrink-0 text-slate-400 hover:text-slate-200 text-xl leading-none">×</button>
          } />
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
              <ProjectDetailTabs tabs={([['knowledge', 'Knowledge Graph'], ['workflow', 'Workflow Cards']] as const)} active={graphView} onChange={setGraphView} />
            </div>
            {graphView === 'knowledge' && <Suspense fallback={<p className="text-sm text-slate-400">Loading graph…</p>}><GraphTab projectId={project.project_id} /></Suspense>}
            {graphView === 'workflow' && (
              <Suspense fallback={<p className="text-sm text-slate-400">Loading workflow…</p>}>
              <WorkflowGraphTab
                key={`${project.project_id}-${project.workflow_version ?? 0}`}
                projectId={project.project_id}
                actionsDisabled={!!activeJobId}
                onWorkflowVersionDeleted={refreshProject}
              />
              </Suspense>
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
