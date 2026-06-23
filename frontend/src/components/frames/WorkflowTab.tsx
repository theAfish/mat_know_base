import type { Project } from '../../types'
import StatusBadge from '../StatusBadge'
import WorkflowGraphTab from '../projects/WorkflowGraphTab'

export default function WorkflowTab({
  project,
  activeJobId,
  onExtractWorkflow,
  onWorkflowVersionDeleted,
}: {
  project: Project
  activeJobId: string | null
  onExtractWorkflow: () => void
  onWorkflowVersionDeleted?: () => void
}) {
  const workflowActionLabel = project.workflow_status === 'IN_PROGRESS'
    ? '⛓ Resume Workflow'
    : project.workflow_status === 'FAILED'
      ? '⛓ Retry Workflow'
      : '⛓ Extract Workflow'

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-slate-400">Workflow cards</span>
          <StatusBadge status={project.workflow_status ?? 'NO_WORKFLOW'} />
          <span className="text-slate-500">v{project.workflow_version ?? '—'}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onExtractWorkflow}
          disabled={!!activeJobId}
          className="px-3 py-1.5 bg-violet-900/70 hover:bg-violet-800 disabled:opacity-40 rounded text-xs text-violet-100"
        >
          {workflowActionLabel}
        </button>
      </div>

      <WorkflowGraphTab
        projectId={project.project_id}
        actionsDisabled={!!activeJobId}
        onWorkflowVersionDeleted={onWorkflowVersionDeleted}
      />
    </div>
  )
}
