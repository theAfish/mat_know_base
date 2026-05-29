import { useState } from 'react'

import { deleteProjection } from '../../api/projections'
import type { Projection } from '../../types'
import StatusBadge from '../StatusBadge'
import { stringify } from './helpers'


export default function ProjectionRow({
  proj,
  paperLookup,
  onDeleted,
  selected,
  onToggleSelected,
}: {
  proj: Projection
  paperLookup: Record<string, string>
  onDeleted: (id: string) => void
  selected: boolean
  onToggleSelected: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [deleting, setDeleting] = useState(false)

  return (
    <div className={`border rounded-lg overflow-hidden ${
      proj.superseded_by_id
        ? 'bg-slate-900/60 border-slate-700/40 opacity-70'
        : selected
          ? 'bg-teal-900/20 border-teal-700/50'
          : 'bg-slate-800 border-slate-700'
    }`}>
      <div className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-slate-700/50">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelected(proj.projection_id)}
          onClick={e => e.stopPropagation()}
          className="accent-teal-500"
          title="Select for batch actions"
        />
        <button onClick={() => setExpanded(s => !s)} className="flex-1 flex items-center gap-3 text-left">
          <StatusBadge status={proj.status} />
          {proj.superseded_by_id && (
            <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-medium bg-slate-700 text-slate-400 border border-slate-600">
              superseded
            </span>
          )}
          <span className="flex-1 text-sm text-slate-300 truncate">
            {paperLookup[proj.project_id] ?? proj.project_id.slice(0, 12)}
          </span>
          {proj.source_type && (
            <span
              className={
                'px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-medium ' +
                (proj.source_type === 'markdown'
                  ? 'bg-amber-900/40 text-amber-300 border border-amber-700/40'
                  : 'bg-sky-900/40 text-sky-300 border border-sky-700/40')
              }
              title={
                proj.source_type === 'markdown'
                  ? 'Projected directly from processed Markdown (no frame extraction)'
                  : 'Projected from curated knowledge frame'
              }
            >
              {proj.source_type === 'markdown' ? 'md' : 'frame'}
            </span>
          )}
          <span className="text-xs text-slate-500">
            {proj.times_reviewed > 0 ? `Reviewed ${proj.times_reviewed}×` : 'Raw'}
            {' · '}v{proj.space_version}
            {proj.extracted_at ? ` · ${proj.extracted_at.slice(0, 10)}` : ''}
          </span>
          <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
        </button>
      </div>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-slate-700 space-y-3">
          {proj.agent_notes && (
            <p className="text-xs text-slate-400 italic">{proj.agent_notes.slice(0, 300)}{proj.agent_notes.length > 300 ? '…' : ''}</p>
          )}
          {proj.review_notes && (
            <div className="bg-teal-900/30 border border-teal-700/40 rounded px-3 py-2 text-xs text-teal-200">
              {proj.review_notes.slice(0, 200)}
            </div>
          )}
          {proj.data && Object.entries(proj.data).map(([section, items]) => {
            const rows = Array.isArray(items) ? items : []
            return rows.length > 0 ? (
              <div key={section} className="space-y-1">
                <p className="text-xs font-medium text-slate-400 capitalize">{section.replace(/_/g, ' ')} ({rows.length})</p>
                {rows.slice(0, 3).map((item, i) => (
                  <div key={i} className="text-xs text-slate-500 pl-2 truncate">
                    {typeof item === 'object' && item !== null
                      ? Object.entries(item as Record<string, unknown>).slice(0, 3).map(([k, v]) =>
                          `${k}: ${stringify(v)}`).join(' · ')
                      : stringify(item)
                    }
                  </div>
                ))}
                {rows.length > 3 && <p className="text-xs text-slate-600 pl-2">…and {rows.length - 3} more</p>}
              </div>
            ) : null
          })}
          <div className="pt-2 flex justify-end">
            <button
              onClick={async () => {
                if (!confirm('Delete this projection? This cannot be undone.')) return
                setDeleting(true)
                try {
                  await deleteProjection(proj.projection_id)
                  onDeleted(proj.projection_id)
                } catch {
                  alert('Failed to delete projection.')
                  setDeleting(false)
                }
              }}
              disabled={deleting}
              className="px-3 py-1 text-xs rounded bg-red-900/40 hover:bg-red-800/60 text-red-300 border border-red-700/40 disabled:opacity-40"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
