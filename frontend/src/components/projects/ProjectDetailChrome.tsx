import type { ReactNode } from 'react'

import type { Project } from '../../types'
import { projectDisplayName } from '../../utils/projectName'
import StatusBadge from '../StatusBadge'

export function ProjectDetailHeader({
  project,
  leading,
  trailing,
}: {
  project: Project
  leading?: ReactNode
  trailing?: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-3 min-w-0">
        {leading}
        <div className="min-w-0">
          <h3 className="font-semibold text-slate-100 truncate">{projectDisplayName(project)}</h3>
          <p className="text-xs text-slate-400 mt-0.5">{project.asset_count} asset(s)</p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap justify-end">
        <StatusBadge status={project.processing_status ?? 'UNPROCESSED'} />
        <StatusBadge status={project.frame_status ?? 'NO_FRAME'} />
        <StatusBadge status={project.workflow_status ?? 'NO_WORKFLOW'} />
        {trailing}
      </div>
    </div>
  )
}

export function ProjectDetailTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: readonly (readonly [T, string])[]
  active: T
  onChange: (tab: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-slate-700">
      {tabs.map(([tab, label]) => (
        <button key={tab} onClick={() => onChange(tab)}
          className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px whitespace-nowrap ${
            active === tab
              ? 'border-teal-400 text-teal-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}>
          {label}
        </button>
      ))}
    </div>
  )
}
