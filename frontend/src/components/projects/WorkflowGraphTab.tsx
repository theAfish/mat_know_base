import { useEffect, useRef, useState } from 'react'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'

import { getProjectWorkflow, listProjectWorkflows } from '../../api/projects'
import type { RawWorkflowVersion } from '../../types'

function esc(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))
}

function RawWorkflowCanvas({ workflow }: { workflow: RawWorkflowVersion }) {
  const ref = useRef<HTMLDivElement>(null)
  const graph = workflow.graph

  useEffect(() => {
    if (!ref.current || !graph) return
    const nodes = new DataSet(graph.nodes.map(node => ({
      id: node.node_id,
      label: node.raw_name,
      shape: node.node_kind_guess === 'operation' ? 'box' : 'ellipse',
      color: node.node_kind_guess === 'operation'
        ? { background: '#7c3aed', border: '#a78bfa' }
        : { background: '#0f766e', border: '#2dd4bf' },
      title: `<b>${esc(node.raw_name)}</b><br>${esc(node.node_kind_guess)} · confidence ${node.confidence.toFixed(2)}<br><br>${esc(node.evidence_text)}`,
    })))
    const edges = new DataSet(graph.edges.map(edge => ({
      id: edge.edge_id, from: edge.source_node, to: edge.target_node,
      label: edge.relation_type,
      title: `${esc(edge.evidence_text)}<br>confidence ${edge.confidence.toFixed(2)}`,
      arrows: 'to', color: { color: '#64748b' },
    })))
    const network = new Network(ref.current, { nodes, edges }, {
      layout: {
        hierarchical: {
          enabled: true,
          direction: 'UD',
          sortMethod: 'directed',
          shakeTowards: 'roots',
          levelSeparation: 120,
          nodeSpacing: 180,
          treeSpacing: 220,
          blockShifting: true,
          edgeMinimization: true,
          parentCentralization: true,
        },
      },
      physics: false,
      nodes: { font: { color: '#f1f5f9', size: 13 }, margin: { top: 10, right: 10, bottom: 10, left: 10 }, borderWidth: 1 },
      edges: {
        font: { color: '#94a3b8', size: 10, align: 'middle' },
        smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.35 },
      },
      interaction: { hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: true },
    })
    return () => network.destroy()
  }, [graph])

  if (!graph || graph.nodes.length === 0) return <p className="text-sm text-slate-400">This version contains no explicitly supported workflow steps.</p>
  return <div ref={ref} className="h-[430px] rounded-lg border border-slate-700 bg-slate-950" />
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
      <p className="text-xs text-slate-500">Purple rectangles are operations; teal ellipses are objects. Hover nodes and edges for evidence.</p>
    </div>
  )
}
