import { useCallback, useEffect, useState } from 'react'

import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import { listProjections } from '../../api/projections'
import { listSpaces } from '../../api/spaces'
import type { Job, Projection, Space } from '../../types'
import StatusBadge from '../StatusBadge'
import { FrameSection } from './frameRender'


export default function ProjectionsTab({ projectId }: { projectId: string }) {
  const [projections, setProjections] = useState<Projection[]>([])
  const [spaces, setSpaces] = useState<Space[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback((showLoading = false) => {
    if (showLoading) setLoading(true)
    Promise.all([
      listProjections({ project_id: projectId, include_data: true, limit: 50 }),
      listSpaces(),
    ]).then(([projs, sps]) => {
      setProjections(projs)
      setSpaces(sps.filter(s => s.name !== '__global_kg__'))
    }).finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (
        job.status === 'COMPLETED' &&
        job.project_id === projectId &&
        ['project', 'projection_review'].includes(job.kind)
      ) {
        load()
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load, projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (projections.length === 0) return <p className="text-sm text-slate-400">No projections yet. Select a space and run Project.</p>

  const spaceMap: Record<string, string> = {}
  spaces.forEach(s => { spaceMap[s.space_id] = s.name })

  return (
    <div className="space-y-3">
      {projections.map(p => (
        <details key={p.projection_id} className="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
          <summary className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-slate-800">
            <StatusBadge status={p.status} />
            <span className="text-sm text-slate-200 font-medium">{spaceMap[p.space_id] ?? p.space_id.slice(0, 12)}</span>
            <span className="text-xs text-slate-400 ml-auto">
              v{p.space_version} · {p.times_reviewed}× reviewed · {p.extracted_at?.slice(0, 10) ?? '—'}
            </span>
          </summary>
          <div className="px-4 pb-4 pt-2 border-t border-slate-700 space-y-3">
            {p.agent_notes && <p className="text-xs text-slate-400 italic">{p.agent_notes.slice(0, 300)}</p>}
            {p.review_notes && (
              <div className="bg-teal-900/30 border border-teal-700/40 rounded px-3 py-2 text-xs text-teal-200">{p.review_notes}</div>
            )}
            {p.data && (
              <div className="space-y-2">
                {Object.entries(p.data).map(([section, value]) => (
                  <FrameSection key={section} name={section} value={value} depth={2} defaultOpen={true} />
                ))}
              </div>
            )}
          </div>
        </details>
      ))}
    </div>
  )
}
