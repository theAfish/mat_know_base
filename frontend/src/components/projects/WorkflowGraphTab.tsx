import { useEffect, useState } from 'react'

import { deleteProjectWorkflowVersion, getProjectWorkflow, listProjectWorkflows } from '../../api/projects'
import type { RawWorkflowVersion } from '../../types'
import WorkflowCanvas, { type WorkflowCanvasEdge, type WorkflowCanvasNode } from './WorkflowCanvas'

function workflowNodeKind(node: { node_kind?: string; node_kind_guess: string }): WorkflowCanvasNode['kind'] {
  const kind = node.node_kind ?? node.node_kind_guess
  if (kind === 'operation' || kind === 'planning' || kind === 'reasoning' || kind === 'unknown') return kind
  return 'object'
}

function RawWorkflowCanvas({ workflow }: { workflow: RawWorkflowVersion }) {
  const graph = workflow.graph

  if (!graph || graph.nodes.length === 0) return <p className="text-sm text-slate-400">This version contains no explicitly supported workflow steps.</p>

  const nodes: WorkflowCanvasNode[] = graph.nodes.map(node => ({
    id: node.node_id,
    label: node.canonical_name ?? node.raw_name,
    kind: workflowNodeKind(node),
    title: `${node.canonical_name ?? node.raw_name}\nsource term: ${node.raw_name}\n${node.semantic_type ?? node.node_kind_guess} · confidence ${node.confidence.toFixed(2)}\n\n${node.evidence_text}`,
    details: {
      card_id: node.card_id,
      ontology_status: node.ontology_status,
      semantic_type: node.semantic_type,
      parameters: node.parameters,
      identity: node.identity,
      state: node.state,
      role: node.role,
      context: node.context,
      unparsed_modifiers: node.unparsed_modifiers,
      confidence: node.confidence,
      attributes_explicitly_mentioned: node.attributes_explicitly_mentioned,
      paper_location: node.paper_location,
      evidence_text: node.evidence_text,
    },
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

function formatReviewFlag(flag: { type: string; item_type?: string; item_id?: string }) {
  const label = flag.type.replace(/_/g, ' ')
  return flag.item_id ? `${label}: ${flag.item_id}` : label
}

export default function WorkflowGraphTab({
  projectId,
  actionsDisabled = false,
  onWorkflowVersionDeleted,
}: {
  projectId: string
  actionsDisabled?: boolean
  onWorkflowVersionDeleted?: () => void
}) {
  const [versions, setVersions] = useState<RawWorkflowVersion[]>([])
  const [selected, setSelected] = useState<RawWorkflowVersion | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingVersion, setDeletingVersion] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await listProjectWorkflows(projectId)
      setVersions(rows)
      const preferred = rows.find(row => row.status === 'COMPLETED') ?? rows[0] ?? null
      setSelected(preferred ? await getProjectWorkflow(projectId, preferred.version) : null)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to load workflow versions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [projectId])

  const choose = async (version: number) => setSelected(await getProjectWorkflow(projectId, version))

  const handleDelete = async (version: number) => {
    const target = versions.find(row => row.version === version)
    if (!target) return
    const confirmed = window.confirm(
      `Delete raw workflow v${version}? This removes the selected raw workflow version.`,
    )
    if (!confirmed) return
    try {
      setDeletingVersion(version)
      setError(null)
      await deleteProjectWorkflowVersion(projectId, version)
      await load()
      onWorkflowVersionDeleted?.()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to delete workflow version')
    } finally {
      setDeletingVersion(null)
    }
  }

  if (loading) return <p className="text-sm text-slate-400">Loading workflow…</p>
  if (error && versions.length === 0) return <p className="text-sm text-red-400">{error}</p>
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
        {selected && (
          <button
            onClick={() => handleDelete(selected.version)}
            disabled={actionsDisabled || deletingVersion === selected.version}
            className="rounded border border-red-800/70 bg-red-950/40 px-2 py-1 text-red-200 hover:bg-red-900/40 disabled:opacity-40"
          >
            {deletingVersion === selected.version ? 'Deleting…' : 'Delete Version'}
          </button>
        )}
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {selected?.error && <p className="text-sm text-red-400">{selected.error}</p>}
      {selected?.review_flags && selected.review_flags.length > 0 && (
        <div className="rounded border border-amber-700/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
          {selected.review_flags.slice(0, 4).map(flag => formatReviewFlag(flag)).join(' · ')}
          {selected.review_flags.length > 4 ? ` · ${selected.review_flags.length - 4} more` : ''}
        </div>
      )}
      {selected && !selected.graph && selected.resumable && (
        <div className="rounded border border-amber-700/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          {selected.has_checkpoint
            ? `This unfinished version has a saved checkpoint${selected.checkpoint_updated_at ? ` from ${new Date(selected.checkpoint_updated_at).toLocaleString()}` : ''}. Run Extract Workflow again to resume the same version instead of creating a new one.`
            : 'This unfinished version has no finalized graph yet. Run Extract Workflow again to resume the same version.'}
          {selected.checkpoint_summary ? <p className="mt-1 text-xs text-amber-300/90">{selected.checkpoint_summary}</p> : null}
        </div>
      )}
      {selected?.graph ? <RawWorkflowCanvas workflow={selected} /> : null}
      <p className="text-xs text-slate-500">Purple rectangles are operations; teal parallelograms are objects; amber and blue rectangles are planning and reasoning. Drag nodes freely to tidy the canvas and hover nodes for evidence.</p>
    </div>
  )
}
