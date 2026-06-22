import { useEffect, useRef, useState } from 'react'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'

import { getCanonicalWorkflow, listCanonicalWorkflows } from '../../api/projects'
import type { CanonicalWorkflowVersion } from '../../types'

const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))

function Canvas({ workflow }: { workflow: CanonicalWorkflowVersion }) {
  const ref = useRef<HTMLDivElement>(null)
  const graph = workflow.graph
  useEffect(() => {
    if (!ref.current || !graph) return
    const nodes = new DataSet(graph.nodes.map(node => ({
      id: node.node_id, label: node.label,
      shape: node.node_kind === 'operation' ? 'box' : 'ellipse',
      color: node.node_kind === 'operation'
        ? { background: '#4338ca', border: '#818cf8' }
        : { background: '#0369a1', border: '#38bdf8' },
      title: `<b>${esc(node.label)}</b><br>${esc(node.object_schema ?? node.operation_template_id ?? 'unmatched template')}<br><br>Raw sources: ${esc(node.raw_node_ids.join(', '))}<br>Attributes: ${esc(JSON.stringify(node.attributes))}`,
    })))
    const edges = new DataSet(graph.edges.map(edge => ({
      id: edge.edge_id, from: edge.source_node, to: edge.target_node,
      label: edge.relation_type, arrows: 'to', color: { color: '#64748b' },
      title: `Raw edges: ${esc(edge.raw_edge_ids.join(', '))}`,
    })))
    const network = new Network(ref.current, { nodes, edges }, {
      layout: { hierarchical: { enabled: true, direction: 'UD', sortMethod: 'directed', shakeTowards: 'roots', levelSeparation: 120, nodeSpacing: 180, treeSpacing: 220, blockShifting: true, edgeMinimization: true, parentCentralization: true } },
      physics: false,
      nodes: { font: { color: '#f1f5f9', size: 13 }, margin: { top: 10, right: 10, bottom: 10, left: 10 }, borderWidth: 1 },
      edges: { font: { color: '#94a3b8', size: 10, align: 'middle' }, smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.35 } },
      interaction: { hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: true },
    })
    return () => network.destroy()
  }, [graph])
  if (!graph?.nodes.length) return <p className="text-sm text-slate-400">This canonical version has no nodes.</p>
  return <div ref={ref} className="h-[430px] rounded-lg border border-slate-700 bg-slate-950" />
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
    {selected?.graph && <div className="flex gap-4 text-xs text-slate-500">
      <span>{selected.graph.raw_to_canonical_mappings.length} mappings</span>
      <span>{selected.graph.unmatched_raw_information.length} unmatched items</span>
      <span>{selected.graph.proposed_schema_updates.length} schema proposals</span>
    </div>}
  </div>
}
