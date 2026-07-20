import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import { listFeedback } from '../../api/feedback'
import {
  extractProject,
  kgExtractProject,
  processProject,
  projectToSpace,
  workflowExtractProject,
} from '../../api/projects'
import { listSpaces } from '../../api/spaces'
import { useProjectRefresh } from '../../hooks/useProjectRefresh'
import { useProjectJobController } from '../../hooks/useProjectJobController'
import type { Project, Space } from '../../types'
import { ProjectDetailHeader, ProjectDetailTabs } from '../projects/ProjectDetailChrome'
const AssetsTab = lazy(() => import('./AssetsTab'))
const FeedbackTab = lazy(() => import('./FeedbackTab'))
const GraphTab = lazy(() => import('./GraphTab'))
const KnowledgeFrameTab = lazy(() => import('./KnowledgeFrameTab'))
const ProjectionsTab = lazy(() => import('./ProjectionsTab'))
const WorkflowTab = lazy(() => import('./WorkflowTab'))


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
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [projectionSource, setProjectionSource] = useState<'frame' | 'markdown'>('frame')
  const [feedbackCount, setFeedbackCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)

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

  const refreshProject = useProjectRefresh(project.project_id, onProjectUpdated)
  const [afterAction, setAfterAction] = useState<DetailTab | null>(null)
  const { activeJobId, activeJob, actionError, run } = useProjectJobController({
    onComplete: () => {
      if (afterAction) setActiveTab(afterAction)
      setAfterAction(null)
      setRefreshKey(k => k + 1)
      refreshFeedbackCount()
      refreshProject()
    },
    onSettled: () => setAfterAction(null),
  })

  const runAction = (fn: () => Promise<{ job_id: string }>, nextTab?: DetailTab) => {
    setAfterAction(nextTab ?? null)
    void run(fn)
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 py-4 border-b border-slate-700 flex-shrink-0 space-y-3">
        <ProjectDetailHeader project={project} leading={
          <button onClick={onBack} className="text-sm text-teal-400 hover:text-teal-300 flex-shrink-0">← Back</button>
        } />
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => runAction(() => processProject(project.project_id))} disabled={!!activeJobId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            ⚙ Process
          </button>
          <button onClick={() => runAction(() => extractProject(project.project_id))} disabled={!!activeJobId}
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
          <button onClick={() => runAction(() => projectToSpace(project.project_id, selectedSpaceId, projectionSource), 'projections')}
            disabled={!!activeJobId || !selectedSpaceId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            🗂 Project
          </button>
          <button onClick={() => runAction(() => kgExtractProject(project.project_id), 'graph')}
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

      <div className="px-6 pt-3 flex-shrink-0">
        <ProjectDetailTabs tabs={[
          ['assets', 'Assets'],
          ['frame', 'Knowledge Frame'],
          ['projections', 'Projections'],
          ['workflow', 'Workflow'],
          ['graph', 'Knowledge Graph'],
          ['feedback', `Feedback${feedbackCount > 0 ? ` (${feedbackCount})` : ''}`],
        ] as const} active={activeTab} onChange={setActiveTab} />
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <Suspense fallback={<p className="text-sm text-slate-400">Loading detail…</p>}>
        {activeTab === 'assets'      && <AssetsTab key={`assets-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'frame'       && <KnowledgeFrameTab key={`frame-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'projections' && <ProjectionsTab key={`proj-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'workflow'    && <Suspense fallback={<p className="text-sm text-slate-400">Loading workflow…</p>}>
          <WorkflowTab
            key={`workflow-${project.project_id}-${project.workflow_version ?? 0}-${refreshKey}`}
            project={project}
            activeJobId={activeJobId}
            onExtractWorkflow={() => runAction(() => workflowExtractProject(project.project_id))}
            onWorkflowVersionDeleted={() => {
              setRefreshKey(k => k + 1)
              refreshProject()
            }}
          />
        </Suspense>}
        {activeTab === 'graph'       && <Suspense fallback={<p className="text-sm text-slate-400">Loading graph…</p>}><GraphTab key={`graph-${refreshKey}`} projectId={project.project_id} /></Suspense>}
        {activeTab === 'feedback'    && <FeedbackTab key={`fb-${refreshKey}`} projectId={project.project_id} />}
        </Suspense>
      </div>
    </div>
  )
}
