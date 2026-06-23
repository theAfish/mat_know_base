import { useCallback, useEffect, useState } from 'react'

import { startJobPolling } from '../../api/jobPolling'
import { listFeedback } from '../../api/feedback'
import {
  extractProject,
  getProject,
  kgExtractProject,
  processProject,
  projectToSpace,
  workflowExtractProject,
  canonicalizeProjectWorkflow,
} from '../../api/projects'
import { listSpaces } from '../../api/spaces'
import type { Job, Project, Space } from '../../types'
import StatusBadge from '../StatusBadge'
import AssetsTab from './AssetsTab'
import FeedbackTab from './FeedbackTab'
import GraphTab from './GraphTab'
import KnowledgeFrameTab from './KnowledgeFrameTab'
import ProjectionsTab from './ProjectionsTab'
import WorkflowTab from './WorkflowTab'


type DetailTab = 'assets' | 'frame' | 'projections' | 'workflow' | 'graph' | 'feedback'

export default function ProjectDetail({
  project,
  onBack,
  onProjectUpdated,
}: {
  project: Project
  onBack: () => void
  onProjectUpdated?: (p: Project) => void
}) {
  const [activeTab, setActiveTab] = useState<DetailTab>('frame')
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [projectionSource, setProjectionSource] = useState<'frame' | 'markdown'>('frame')
  const [feedbackCount, setFeedbackCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [actionError, setActionError] = useState<string | null>(null)

  const refreshFeedbackCount = useCallback(() => {
    listFeedback({ project_id: project.project_id, limit: 100 })
      .then(items => setFeedbackCount(items.length)).catch(() => {})
  }, [project.project_id])

  useEffect(() => {
    listSpaces().then(sps => {
      const visible = sps.filter(s => s.name !== '__global_kg__')
      setSpaces(visible)
      if (visible.length > 0 && !selectedSpaceId) setSelectedSpaceId(visible[0].space_id)
    }).catch(() => {})
    refreshFeedbackCount()
  }, [project.project_id, refreshFeedbackCount, selectedSpaceId])

  const refreshProject = useCallback(() => {
    if (!onProjectUpdated) return
    getProject(project.project_id).then(onProjectUpdated).catch(() => {})
  }, [project.project_id, onProjectUpdated])

  const pollJob = useCallback((jobId: string, onDone?: () => void) => {
    setActiveJobId(jobId)
    startJobPolling({
      jobId,
      onUpdate: setActiveJob,
      onTick: tick => { if (tick % 3 === 0) refreshProject() },
      onComplete: () => {
        setActiveJobId(null)
        onDone?.()
        setRefreshKey(k => k + 1)
        refreshFeedbackCount()
        refreshProject()
      },
      onFailed: () => setActiveJobId(null),
    })
  }, [refreshFeedbackCount, refreshProject])

  const run = async (fn: () => Promise<{ job_id: string }>, onDone?: () => void) => {
    if (activeJobId) return
    try {
      setActionError(null)
      const { job_id } = await fn()
      pollJob(job_id, onDone)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setActionError(e?.response?.data?.detail ?? e?.message ?? 'Action failed')
    }
  }

  const label = project.label ?? project.source_path ?? project.project_id.slice(0, 12)

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 py-4 border-b border-slate-700 flex-shrink-0 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <button onClick={onBack} className="text-sm text-teal-400 hover:text-teal-300 flex-shrink-0">← Back</button>
            <h3 className="text-base font-semibold text-slate-100 truncate">{label}</h3>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <StatusBadge status={project.processing_status ?? 'UNPROCESSED'} />
            <StatusBadge status={project.frame_status ?? 'NO_FRAME'} />
            <StatusBadge status={project.workflow_status ?? 'NO_WORKFLOW'} />
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => run(() => processProject(project.project_id))} disabled={!!activeJobId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            ⚙ Process
          </button>
          <button onClick={() => run(() => extractProject(project.project_id))} disabled={!!activeJobId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            🧪 Extract
          </button>
          <select value={selectedSpaceId} onChange={e => setSelectedSpaceId(e.target.value)}
            disabled={spaces.length === 0 || !!activeJobId}
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none disabled:opacity-40">
            {spaces.length === 0 ? <option>No spaces</option>
              : spaces.map(s => <option key={s.space_id} value={s.space_id}>{s.name}</option>)}
          </select>
          <select value={projectionSource} onChange={e => setProjectionSource(e.target.value as 'frame' | 'markdown')}
            disabled={!!activeJobId}
            title="Project from the curated knowledge frame, or directly from the processed Markdown"
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none disabled:opacity-40">
            <option value="frame">from frame</option>
            <option value="markdown">from markdown</option>
          </select>
          <button onClick={() => run(() => projectToSpace(project.project_id, selectedSpaceId, projectionSource), () => setActiveTab('projections'))}
            disabled={!!activeJobId || !selectedSpaceId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            🗂 Project
          </button>
          <button onClick={() => run(() => kgExtractProject(project.project_id), () => setActiveTab('graph'))}
            disabled={!!activeJobId}
            className="px-3 py-1.5 bg-teal-700 hover:bg-teal-600 disabled:opacity-40 rounded text-xs text-white">
            🕸 Extract Graph
          </button>
        </div>
        {actionError && (
          <div className="rounded border border-red-700 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {actionError}
          </div>
        )}
        {activeJob && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            {activeJobId && <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />}
            <span>{activeJob.current_message ?? activeJob.label}</span>
            {activeJob.status === 'COMPLETED' && <span className="text-green-400">✓ Done</span>}
            {activeJob.status === 'FAILED' && <span className="text-red-400">✗ {activeJob.error?.slice(0, 80)}</span>}
          </div>
        )}
      </div>

      <div className="px-6 pt-3 border-b border-slate-700 flex flex-wrap gap-1 flex-shrink-0">
        {([
          ['assets', 'Assets'],
          ['frame', 'Knowledge Frame'],
          ['projections', 'Projections'],
          ['workflow', 'Workflow'],
          ['graph', 'Knowledge Graph'],
          ['feedback', `Feedback${feedbackCount > 0 ? ` (${feedbackCount})` : ''}`],
        ] as [DetailTab, string][]).map(([tab, name]) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-3 py-1.5 text-xs font-medium rounded-t whitespace-nowrap transition-colors ${
              activeTab === tab ? 'text-teal-400 border-b-2 border-teal-400 -mb-px' : 'text-slate-400 hover:text-slate-200'
            }`}>
            {name}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'assets'      && <AssetsTab key={`assets-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'frame'       && <KnowledgeFrameTab key={`frame-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'projections' && <ProjectionsTab key={`proj-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'workflow'    && (
          <WorkflowTab
            key={`workflow-${project.project_id}-${project.workflow_version ?? 0}-${project.canonical_workflow_version ?? 0}-${refreshKey}`}
            project={project}
            activeJobId={activeJobId}
            onExtractWorkflow={() => run(() => workflowExtractProject(project.project_id))}
            onCanonicalizeWorkflow={() => run(() => canonicalizeProjectWorkflow(project.project_id))}
            onWorkflowVersionDeleted={() => {
              setRefreshKey(k => k + 1)
              refreshProject()
            }}
          />
        )}
        {activeTab === 'graph'       && <GraphTab key={`graph-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'feedback'    && <FeedbackTab key={`fb-${refreshKey}`} projectId={project.project_id} />}
      </div>
    </div>
  )
}
