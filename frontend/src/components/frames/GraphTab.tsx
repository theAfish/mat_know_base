import { useCallback, useEffect, useState } from 'react'

import { getKnowledgeGraph } from '../../api/graph'
import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import type { GraphConcept, GraphRelation, Job } from '../../types'
import MiniGraph from './MiniGraph'


export default function GraphTab({ projectId }: { projectId: string }) {
  const [concepts, setConcepts] = useState<GraphConcept[]>([])
  const [relations, setRelations] = useState<GraphRelation[]>([])
  const [loading, setLoading] = useState(true)
  const [showList, setShowList] = useState(false)

  const load = useCallback((showLoading = false) => {
    if (showLoading) setLoading(true)
    getKnowledgeGraph({ project_id: projectId })
      .then(d => { setConcepts(d.graph?.concepts ?? []); setRelations(d.graph?.relations ?? []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (
        job.status === 'COMPLETED' &&
        job.project_id === projectId &&
        ['knowledge_graph', 'graph_review'].includes(job.kind)
      ) {
        load()
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load, projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (concepts.length === 0) return <p className="text-sm text-slate-400">No graph elements for this project yet. Run Extract Graph.</p>

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">{concepts.length} concept(s), {relations.length} relation(s)</p>
      <MiniGraph concepts={concepts} relations={relations} />
      <div>
        <button onClick={() => setShowList(s => !s)} className="text-xs text-teal-400 hover:text-teal-300">
          {showList ? '▲ Hide list' : '▼ Show concept / relation list'}
        </button>
        {showList && (
          <div className="mt-2 grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs font-medium text-slate-400 mb-1">Concepts ({concepts.length})</p>
              <div className="space-y-0.5 max-h-48 overflow-y-auto">
                {concepts.map((c, i) => (
                  <div key={i} className="text-xs text-slate-400 px-2 py-1 bg-slate-900 rounded">
                    <span className="text-slate-200">{c.label}</span>
                    {c.aliases && c.aliases.length > 0 && <span className="text-slate-500 ml-1"> ({c.aliases.slice(0, 2).join(', ')})</span>}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-400 mb-1">Relations ({relations.length})</p>
              <div className="space-y-0.5 max-h-48 overflow-y-auto">
                {relations.map((r, i) => (
                  <div key={i} className="text-xs text-slate-500 px-2 py-1 bg-slate-900 rounded">
                    <span className="text-slate-300">{r.source}</span>
                    <span className="text-slate-500 mx-1">→ {r.relation} →</span>
                    <span className="text-slate-300">{r.target}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
