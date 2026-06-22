import { useEffect, useState } from 'react'

import { getCanonicalWorkflow, listCanonicalWorkflows } from '../../api/projects'
import type { CanonicalWorkflowVersion } from '../../types'
import WorkflowCanvas, { type WorkflowCanvasEdge, type WorkflowCanvasNode } from './WorkflowCanvas'

function Canvas({ workflow }: { workflow: CanonicalWorkflowVersion }) {
  const graph = workflow.graph

  if (!graph?.nodes.length) return <p className="text-sm text-slate-400">This canonical version has no nodes.</p>

  const nodes: WorkflowCanvasNode[] = graph.nodes.map(node => ({
    id: node.node_id,
    label: node.label,
    kind: node.node_kind,
    title: `${node.label}\n${node.object_schema ?? node.operation_template_id ?? 'unmatched template'}\n\nRaw sources: ${node.raw_node_ids.join(', ')}\nAttributes: ${JSON.stringify(node.attributes)}`,
  }))
  const edges: WorkflowCanvasEdge[] = graph.edges.map(edge => ({
    id: edge.edge_id,
    source: edge.source_node,
    target: edge.target_node,
    label: edge.relation_type,
    title: `Raw edges: ${edge.raw_edge_ids.join(', ')}`,
  }))

  return <WorkflowCanvas nodes={nodes} edges={edges} exportBaseName={`canonical-workflow-v${workflow.version}`} />
}

export default function CanonicalWorkflowTab({ projectId }: { projectId: string }) {
  const [versions, setVersions] = useState<CanonicalWorkflowVersion[]>([])
  const [selected, setSelected] = useState<CanonicalWorkflowVersion | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    setLoading(true)
    listCanonicalWorkflows(projectId).then(async rows => {
      setVersions(rows)
      const latest = rows.find(row => row.status === 'COMPLETED')
      setSelected(latest ? await getCanonicalWorkflow(projectId, latest.version) : null)
    }).finally(() => setLoading(false))
  }, [projectId])
  const choose = async (version: number) => setSelected(await getCanonicalWorkflow(projectId, version))
  if (loading) return <p className="text-sm text-slate-400">Loading normalized workflow…</p>
  if (!versions.length) return <p className="text-sm text-slate-400">No normalized workflow yet. Run Canonicalize Workflow after raw extraction.</p>
  return <div className="space-y-3">
    <div className="flex items-center gap-3 text-xs text-slate-400">
      <label>Canonical version</label>
      <select value={selected?.version ?? ''} onChange={e => choose(Number(e.target.value))} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200">
        {versions.map(row => <option key={row.canonicalization_id} value={row.version}>v{row.version} · {row.status}</option>)}
      </select>
      {selected && <span>{selected.schema_version} · raw {selected.raw_extraction_id.slice(0, 8)}</span>}
    </div>
    {selected?.error && <p className="text-sm text-red-400">{selected.error}</p>}
    {selected && <Canvas workflow={selected} />}
    <p className="text-xs text-slate-500">Objects start above their earliest consuming step, labels wrap across multiple lines, and you can drag nodes freely anywhere in the canvas.</p>
    {selected?.graph && <div className="flex gap-4 text-xs text-slate-500">
      <span>{selected.graph.raw_to_canonical_mappings.length} mappings</span>
      <span>{selected.graph.unmatched_raw_information.length} unmatched items</span>
      <span>{selected.graph.proposed_schema_updates.length} schema proposals</span>
    </div>}
  </div>
}
