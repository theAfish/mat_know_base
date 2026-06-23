import { useEffect, useState } from 'react'

import { deleteCanonicalWorkflowVersion, getCanonicalWorkflow, listCanonicalWorkflows } from '../../api/projects'
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
    details: {
      object_schema: node.object_schema,
      operation_template_id: node.operation_template_id,
      attributes: node.attributes,
      raw_node_ids: node.raw_node_ids,
    },
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

export default function CanonicalWorkflowTab({
  projectId,
  actionsDisabled = false,
  onWorkflowVersionDeleted,
}: {
  projectId: string
  actionsDisabled?: boolean
  onWorkflowVersionDeleted?: () => void
}) {
  const [versions, setVersions] = useState<CanonicalWorkflowVersion[]>([])
  const [selected, setSelected] = useState<CanonicalWorkflowVersion | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingVersion, setDeletingVersion] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await listCanonicalWorkflows(projectId)
      setVersions(rows)
      const preferred = rows.find(row => row.status === 'COMPLETED') ?? rows[0] ?? null
      setSelected(preferred ? await getCanonicalWorkflow(projectId, preferred.version) : null)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to load canonical workflow versions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [projectId])

  const choose = async (version: number) => setSelected(await getCanonicalWorkflow(projectId, version))

  const handleDelete = async () => {
    if (!selected) return
    const confirmed = window.confirm(
      `Delete canonical workflow v${selected.version}? This removes the selected canonical workflow version.`,
    )
    if (!confirmed) return
    try {
      setDeletingVersion(selected.version)
      setError(null)
      await deleteCanonicalWorkflowVersion(projectId, selected.version)
      await load()
      onWorkflowVersionDeleted?.()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to delete canonical workflow version')
    } finally {
      setDeletingVersion(null)
    }
  }

  if (loading) return <p className="text-sm text-slate-400">Loading normalized workflow…</p>
  if (error && !versions.length) return <p className="text-sm text-red-400">{error}</p>
  if (!versions.length) return <p className="text-sm text-slate-400">No normalized workflow yet. Run Canonicalize Workflow after raw extraction.</p>
  return <div className="space-y-3">
    <div className="flex items-center gap-3 text-xs text-slate-400">
      <label>Canonical version</label>
      <select value={selected?.version ?? ''} onChange={e => choose(Number(e.target.value))} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200">
        {versions.map(row => <option key={row.canonicalization_id} value={row.version}>v{row.version} · {row.status}</option>)}
      </select>
      {selected && <span>{selected.schema_version} · raw {selected.raw_extraction_id.slice(0, 8)}</span>}
      {selected && (
        <button
          onClick={handleDelete}
          disabled={actionsDisabled || deletingVersion === selected.version}
          className="rounded border border-red-800/70 bg-red-950/40 px-2 py-1 text-red-200 hover:bg-red-900/40 disabled:opacity-40"
        >
          {deletingVersion === selected.version ? 'Deleting…' : 'Delete Version'}
        </button>
      )}
    </div>
    {error && <p className="text-sm text-red-400">{error}</p>}
    {selected?.error && <p className="text-sm text-red-400">{selected.error}</p>}
    {selected && !selected.graph && selected.resumable && (
      <div className="rounded border border-amber-700/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
        {selected.has_checkpoint
          ? `This unfinished canonical version has a saved checkpoint${selected.checkpoint_updated_at ? ` from ${new Date(selected.checkpoint_updated_at).toLocaleString()}` : ''}. Run Canonicalize Workflow again to resume the same version instead of creating a new one.`
          : 'This unfinished canonical version has no finalized graph yet. Run Canonicalize Workflow again to resume the same version.'}
        {selected.checkpoint_summary ? <p className="mt-1 text-xs text-amber-300/90">{selected.checkpoint_summary}</p> : null}
      </div>
    )}
    {selected?.graph ? <Canvas workflow={selected} /> : null}
    <p className="text-xs text-slate-500">Objects start above their earliest consuming step, labels wrap across multiple lines, and you can drag nodes freely anywhere in the canvas.</p>
    {selected?.graph && <div className="flex gap-4 text-xs text-slate-500">
      <span>{selected.graph.raw_to_canonical_mappings.length} mappings</span>
      <span>{selected.graph.unmatched_raw_information.length} unmatched items</span>
      <span>{selected.graph.proposed_schema_updates.length} schema proposals</span>
    </div>}
  </div>
}
