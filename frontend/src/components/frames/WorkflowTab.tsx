import { useState } from 'react'

import type { Project } from '../../types'
import StatusBadge from '../StatusBadge'
import CanonicalWorkflowTab from '../projects/CanonicalWorkflowTab'
import WorkflowGraphTab from '../projects/WorkflowGraphTab'

export default function WorkflowTab({
  project,
  activeJobId,
  onExtractWorkflow,
  onCanonicalizeWorkflow,
}: {
  project: Project
  activeJobId: string | null
  onExtractWorkflow: () => void
  onCanonicalizeWorkflow: () => void
}) {
  const [view, setView] = useState<'raw' | 'canonical'>('raw')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-slate-400">Raw workflow</span>
          <StatusBadge status={project.workflow_status ?? 'NO_WORKFLOW'} />
          <span className="text-slate-500">v{project.workflow_version ?? '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-400">Canonical workflow</span>
          <StatusBadge status={project.canonical_workflow_status ?? 'NO_CANONICAL_WORKFLOW'} />
          <span className="text-slate-500">v{project.canonical_workflow_version ?? '—'}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onExtractWorkflow}
          disabled={!!activeJobId}
          className="px-3 py-1.5 bg-violet-900/70 hover:bg-violet-800 disabled:opacity-40 rounded text-xs text-violet-100"
        >
          ⛓ Extract Workflow
        </button>
        <button
          onClick={onCanonicalizeWorkflow}
          disabled={!!activeJobId || project.workflow_status !== 'COMPLETED'}
          className="px-3 py-1.5 bg-indigo-900/70 hover:bg-indigo-800 disabled:opacity-40 rounded text-xs text-indigo-100"
        >
          ◇ Canonicalize Workflow
        </button>
      </div>

      <div className="flex gap-1 border-b border-slate-700">
        {([
          ['raw', 'Raw Workflow'],
          ['canonical', 'Canonical Workflow'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setView(key)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px ${
              view === key
                ? 'border-violet-400 text-violet-300'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'raw' && <WorkflowGraphTab projectId={project.project_id} />}
      {view === 'canonical' && <CanonicalWorkflowTab projectId={project.project_id} />}
    </div>
  )
}
