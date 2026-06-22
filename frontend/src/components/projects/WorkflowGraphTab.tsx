import { useEffect, useState } from 'react'

import { getProjectWorkflow, listProjectWorkflows } from '../../api/projects'
import type { RawWorkflowVersion } from '../../types'
import WorkflowCanvas, { type WorkflowCanvasEdge, type WorkflowCanvasNode } from './WorkflowCanvas'

function RawWorkflowCanvas({ workflow }: { workflow: RawWorkflowVersion }) {
  const graph = workflow.graph

  if (!graph || graph.nodes.length === 0) return <p className="text-sm text-slate-400">This version contains no explicitly supported workflow steps.</p>

  const nodes: WorkflowCanvasNode[] = graph.nodes.map(node => ({
    id: node.node_id,
    label: node.raw_name,
    kind: node.node_kind_guess === 'operation' ? 'operation' : 'object',
    title: `${node.raw_name}\n${node.node_kind_guess} · confidence ${node.confidence.toFixed(2)}\n\n${node.evidence_text}`,
  }))
  const edges: WorkflowCanvasEdge[] = graph.edges.map(edge => ({
    id: edge.edge_id,
    source: edge.source_node,
    target: edge.target_node,
    label: edge.relation_type,
    title: `${edge.evidence_text}\nconfidence ${edge.confidence.toFixed(2)}`,
  }))

  return <WorkflowCanvas nodes={nodes} edges={edges} exportBaseName={`raw-workflow-v${workflow.version}`} />
}

export default function WorkflowGraphTab({ projectId }: { projectId: string }) {
  const [versions, setVersions] = useState<RawWorkflowVersion[]>([])
  const [selected, setSelected] = useState<RawWorkflowVersion | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    listProjectWorkflows(projectId)
      .then(async rows => {
        setVersions(rows)
        const latest = rows.find(row => row.status === 'COMPLETED')
        setSelected(latest ? await getProjectWorkflow(projectId, latest.version) : null)
      })
      .finally(() => setLoading(false))
  }, [projectId])

  const choose = async (version: number) => setSelected(await getProjectWorkflow(projectId, version))

  if (loading) return <p className="text-sm text-slate-400">Loading workflow…</p>
  if (versions.length === 0) return <p className="text-sm text-slate-400">No workflow extraction yet. Run Extract Workflow.</p>

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-xs text-slate-400">
        <label>Raw version</label>
        <select value={selected?.version ?? ''} onChange={e => choose(Number(e.target.value))}
          className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200">
          {versions.map(row => <option key={row.extraction_id} value={row.version}>v{row.version} · {row.status}</option>)}
        </select>
        {selected && <span>{selected.graph?.nodes.length ?? 0} nodes · {selected.graph?.edges.length ?? 0} edges · {selected.schema_version}</span>}
      </div>
      {selected?.error && <p className="text-sm text-red-400">{selected.error}</p>}
      {selected && <RawWorkflowCanvas workflow={selected} />}
      <p className="text-xs text-slate-500">Purple rectangles are operations; teal parallelograms are objects. Drag nodes freely to tidy the canvas and hover nodes for evidence.</p>
    </div>
  )
}
