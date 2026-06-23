import { useCallback, useEffect, useRef, useState } from 'react'

import { startJobPolling, type JobPollHandle } from '../../api/jobPolling'
import {
  curateWorkflowSchema,
  editSchemaProposal,
  getSchemaProposalRevisions,
  getWorkflowSchemaStatus,
  listPendingRecanonicalizations,
  listSchemaProposals,
  reviewSchemaProposal,
  runPendingRecanonicalizations,
} from '../../api/workflowSchema'
import type {
  SchemaProposal,
  SchemaProposalRevision,
  SchemaProposalStatus,
  WorkflowMaintenanceTask,
  WorkflowSchemaStatus,
} from '../../types'

const FILTERS: Array<{ key: SchemaProposalStatus | 'all'; label: string }> = [
  { key: 'pending', label: 'Pending review' },
  { key: 'revision_requested', label: 'Needs revision' },
  { key: 'approved', label: 'Approved' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'all', label: 'All' },
]

const TYPE_LABELS: Record<string, string> = {
  create_template: 'Create template',
  merge_templates: 'Merge templates',
  add_alias: 'Add alias',
  add_slot: 'Add parameter slot',
  add_granularity_relation: 'Add granularity relation',
  deprecate_template: 'Deprecate template',
}

function statusClass(status: string) {
  if (status === 'approved') return 'border-emerald-700 bg-emerald-950/40 text-emerald-300'
  if (status === 'rejected') return 'border-rose-800 bg-rose-950/30 text-rose-300'
  if (status === 'revision_requested') return 'border-orange-700 bg-orange-950/30 text-orange-300'
  return 'border-amber-700 bg-amber-950/30 text-amber-300'
}

function RevisionHistory({ proposalId }: { proposalId: string }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [revisions, setRevisions] = useState<SchemaProposalRevision[]>([])

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && revisions.length === 0) {
      setLoading(true)
      try { setRevisions(await getSchemaProposalRevisions(proposalId)) } finally { setLoading(false) }
    }
  }

  return (
    <div className="border-t border-slate-700/70 pt-2">
      <button onClick={toggle} className="text-xs text-slate-400 hover:text-slate-200">
        {open ? '▾' : '▸'} Revision history
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {loading ? <p className="text-xs text-slate-500">Loading revisions…</p> : revisions.map((revision, index) => {
            const previous = revisions[index + 1]
            const changedKeys = previous
              ? Array.from(new Set([
                ...Object.keys(previous.payload),
                ...Object.keys(revision.payload),
              ])).filter(key => JSON.stringify(previous.payload[key]) !== JSON.stringify(revision.payload[key]))
              : []
            const otherChanges = previous ? [
              previous.rationale !== revision.rationale ? 'rationale' : null,
              JSON.stringify(previous.evidence_workflow_ids) !== JSON.stringify(revision.evidence_workflow_ids) ? 'evidence' : null,
            ].filter(Boolean) : []
            const changes = [...changedKeys, ...otherChanges]
            return <div key={revision.revision_id} className="rounded bg-slate-900/70 p-2 text-xs">
              <div className="flex justify-between gap-2 text-slate-400">
                <span>Revision {revision.revision_number} · {revision.author_type}</span>
                <span>{revision.created_at ? new Date(revision.created_at).toLocaleString() : ''}</span>
              </div>
              <p className="mt-1 text-slate-300">{revision.author}</p>
              {revision.change_note && <p className="mt-1 text-slate-500">{revision.change_note}</p>}
              <p className="mt-1 text-[11px] text-teal-400">
                {previous ? (changes.length ? `Changed: ${changes.join(', ')}` : 'Review snapshot; draft unchanged') : 'Original draft'}
              </p>
              <details className="mt-1">
                <summary className="cursor-pointer text-slate-500">Snapshot</summary>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-[11px] text-slate-400">
                  {JSON.stringify(revision.payload, null, 2)}
                </pre>
              </details>
            </div>
          })}
        </div>
      )}
    </div>
  )
}

function ProposalCard({
  proposal,
  reviewer,
  busy,
  onReview,
  onEdit,
}: {
  proposal: SchemaProposal
  reviewer: string
  busy: boolean
  onReview: (
    proposal: SchemaProposal,
    decision: 'approve' | 'reject' | 'request_revision',
    notes: string,
  ) => void
  onEdit: (
    proposal: SchemaProposal,
    draft: Parameters<typeof editSchemaProposal>[1],
  ) => void
}) {
  const [editing, setEditing] = useState(false)
  const [payloadText, setPayloadText] = useState(JSON.stringify(proposal.payload, null, 2))
  const [evidenceText, setEvidenceText] = useState(proposal.evidence_workflow_ids.join('\n'))
  const [rationale, setRationale] = useState(proposal.rationale ?? '')
  const [changeNote, setChangeNote] = useState('')
  const [reviewNotes, setReviewNotes] = useState(proposal.reviewer_notes ?? '')
  const [draftError, setDraftError] = useState<string | null>(null)
  const signal = typeof proposal.analysis.signal === 'string' ? proposal.analysis.signal : null
  const support = typeof proposal.analysis.support === 'number'
    ? proposal.analysis.support
    : proposal.evidence_workflow_ids.length
  const editable = ['pending', 'revision_requested'].includes(proposal.status)

  const saveEdit = () => {
    setDraftError(null)
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(payloadText) as Record<string, unknown>
    } catch {
      setDraftError('Payload must be valid JSON.')
      return
    }
    if (!changeNote.trim()) {
      setDraftError('Describe what changed before saving a revision.')
      return
    }
    onEdit(proposal, {
      payload,
      evidence_workflow_ids: evidenceText.split('\n').map(value => value.trim()).filter(Boolean),
      rationale,
      editor: reviewer.trim(),
      change_note: changeNote.trim(),
    })
    setEditing(false)
  }

  return (
    <article className="rounded-lg border border-slate-700 bg-slate-800/70 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="font-medium text-slate-100">
              {TYPE_LABELS[proposal.proposal_type] ?? proposal.proposal_type.replace(/_/g, ' ')}
            </h4>
            <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${statusClass(proposal.status)}`}>
              {proposal.status.replace(/_/g, ' ')}
            </span>
            <span className="rounded-full border border-slate-600 px-2 py-0.5 text-[11px] text-slate-400">
              {proposal.created_by.includes('schema-curator-agent') ? 'LLM agent draft' : 'deterministic draft'}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Based on {proposal.base_schema_version} · {support} supporting workflow{support === 1 ? '' : 's'}
            {signal ? ` · ${signal.replace(/_/g, ' ')}` : ''}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">Authored by {proposal.created_by}</p>
        </div>
        <span className="text-[11px] text-slate-500 font-mono">{proposal.proposal_id.slice(0, 8)}</span>
      </div>

      {proposal.rationale && (
        <div className="rounded border border-slate-700 bg-slate-900/40 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">Rationale</p>
          <p className="mt-1 text-sm text-slate-300">{proposal.rationale}</p>
        </div>
      )}

      {proposal.validation_errors.length > 0 && (
        <div className="rounded border border-orange-800 bg-orange-950/30 px-3 py-2 text-xs text-orange-200">
          <p className="font-medium">This draft cannot be approved yet:</p>
          <ul className="mt-1 list-disc pl-4">
            {proposal.validation_errors.map(error => <li key={error}>{error}</li>)}
          </ul>
        </div>
      )}

      {editing ? (
        <div className="space-y-3 rounded border border-teal-800/70 bg-slate-900/50 p-3">
          <label className="block text-xs text-slate-400">
            Proposal payload (JSON)
            <textarea value={payloadText} onChange={event => setPayloadText(event.target.value)} rows={8}
              className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 font-mono text-xs text-slate-200" />
          </label>
          <label className="block text-xs text-slate-400">
            Rationale
            <textarea value={rationale} onChange={event => setRationale(event.target.value)} rows={3}
              className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 text-sm text-slate-200" />
          </label>
          <label className="block text-xs text-slate-400">
            Evidence workflow IDs (one per line)
            <textarea value={evidenceText} onChange={event => setEvidenceText(event.target.value)} rows={3}
              className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 font-mono text-xs text-slate-200" />
          </label>
          <label className="block text-xs text-slate-400">
            Revision note
            <input value={changeNote} onChange={event => setChangeNote(event.target.value)}
              className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5 text-sm text-slate-200" />
          </label>
          {draftError && <p className="text-xs text-red-300">{draftError}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={() => setEditing(false)} className="px-3 py-1.5 text-sm text-slate-400">Cancel</button>
            <button onClick={saveEdit} disabled={busy || !reviewer.trim()}
              className="rounded bg-teal-700 px-3 py-1.5 text-sm text-white disabled:opacity-40">Save revision</button>
          </div>
        </div>
      ) : (
        <dl className="grid gap-2 sm:grid-cols-2">
          {Object.entries(proposal.payload).map(([key, value]) => (
            <div key={key} className="rounded bg-slate-900/70 px-3 py-2 min-w-0">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">{key.replace(/_/g, ' ')}</dt>
              <dd className="mt-0.5 text-sm text-slate-200 break-words">
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
          Evidence workflows ({proposal.evidence_workflow_ids.length})
        </summary>
        <div className="mt-2 rounded bg-slate-900/70 p-2 font-mono text-slate-400 space-y-1">
          {proposal.evidence_workflow_ids.map(id => <div key={id}>{id}</div>)}
        </div>
      </details>

      <RevisionHistory proposalId={proposal.proposal_id} />

      {editable && !editing ? (
        <div className="space-y-2 pt-1 border-t border-slate-700/70">
          <textarea
            value={reviewNotes}
            onChange={event => setReviewNotes(event.target.value)}
            placeholder="Reviewer notes (required when requesting revision)"
            rows={2}
            className="w-full rounded border border-slate-600 bg-slate-900 p-2 text-xs text-slate-200"
          />
          <div className="flex justify-between gap-2 flex-wrap">
            <button onClick={() => setEditing(true)} disabled={busy || !reviewer.trim()}
              className="rounded border border-teal-800 px-3 py-1.5 text-sm text-teal-300 disabled:opacity-40">Edit draft</button>
            <div className="flex gap-2">
              <button onClick={() => onReview(proposal, 'reject', reviewNotes)} disabled={busy || !reviewer.trim()}
                className="rounded border border-rose-800 px-3 py-1.5 text-sm text-rose-300 disabled:opacity-40">Reject</button>
              <button onClick={() => onReview(proposal, 'request_revision', reviewNotes)}
                disabled={busy || !reviewer.trim() || !reviewNotes.trim()}
                className="rounded border border-orange-700 px-3 py-1.5 text-sm text-orange-300 disabled:opacity-40">Request revision</button>
              <button onClick={() => onReview(proposal, 'approve', reviewNotes)}
                disabled={busy || !reviewer.trim() || proposal.validation_errors.length > 0}
                className="rounded bg-teal-700 px-3 py-1.5 text-sm text-white disabled:opacity-40">Approve globally</button>
            </div>
          </div>
        </div>
      ) : !editable ? (
        <p className="pt-1 border-t border-slate-700/70 text-xs text-slate-500">
          Reviewed by {proposal.reviewed_by ?? 'unknown reviewer'}
          {proposal.reviewed_at ? ` · ${new Date(proposal.reviewed_at).toLocaleString()}` : ''}
          {proposal.reviewer_notes ? ` · ${proposal.reviewer_notes}` : ''}
        </p>
      ) : null}
    </article>
  )
}

export default function SchemaCuratorTab() {
  const [schema, setSchema] = useState<WorkflowSchemaStatus | null>(null)
  const [proposals, setProposals] = useState<SchemaProposal[]>([])
  const [maintenanceTasks, setMaintenanceTasks] = useState<WorkflowMaintenanceTask[]>([])
  const [filter, setFilter] = useState<SchemaProposalStatus | 'all'>('pending')
  const [reviewer, setReviewer] = useState('human-reviewer')
  const [minSupport, setMinSupport] = useState(2)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [curating, setCurating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [batchRunning, setBatchRunning] = useState(false)
  const curatorPollRef = useRef<JobPollHandle | null>(null)
  const maintenancePollRef = useRef<JobPollHandle | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [schemaData, proposalData, taskData] = await Promise.all([
        getWorkflowSchemaStatus(),
        listSchemaProposals(filter === 'all' ? undefined : filter),
        listPendingRecanonicalizations(),
      ])
      setSchema(schemaData)
      setProposals(proposalData)
      setMaintenanceTasks(taskData)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load schema curator data')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    load()
    return () => {
      curatorPollRef.current?.cancel()
      maintenancePollRef.current?.cancel()
    }
  }, [load])

  const runCurator = async () => {
    setCurating(true)
    setError(null)
    setNotice(null)
    try {
      const result = await curateWorkflowSchema(minSupport, 'schema-curator/ui')
      setNotice(`LLM curator job ${result.job_id.slice(0, 8)} started. Evidence discovery runs before proposal drafting.`)
      curatorPollRef.current?.cancel()
      curatorPollRef.current = startJobPolling({
        jobId: result.job_id,
        onComplete: job => {
          setCurating(false)
          const count = Number(job.result?.proposal_count ?? 0)
          const revisions = Number(job.result?.revision_count ?? 0)
          setNotice(`LLM analysis complete: ${count} new proposal${count === 1 ? '' : 's'} and ${revisions} revision${revisions === 1 ? '' : 's'} drafted.`)
          setFilter('pending')
          load()
        },
        onFailed: job => {
          setCurating(false)
          setError(job?.error ?? 'Schema curator agent failed')
        },
      })
    } catch (err) {
      setCurating(false)
      setError(err instanceof Error ? err.message : 'Schema analysis failed')
    }
  }

  const review = async (
    proposal: SchemaProposal,
    decision: 'approve' | 'reject' | 'request_revision',
    notes: string,
  ) => {
    if (!window.confirm(`Apply review decision “${decision.replace(/_/g, ' ')}”?`)) return
    setBusyId(proposal.proposal_id)
    setError(null)
    try {
      const result = await reviewSchemaProposal(
        proposal.proposal_id, decision, reviewer.trim(), notes,
      )
      setNotice(decision === 'approve'
        ? `Approved as ${result.schema_version}${result.rebased_from_schema ? ` after rebasing from ${result.rebased_from_schema}` : ''}. Rebuild queues: ${result.queues_created ?? 0} new, ${result.queues_updated ?? 0} updated to the latest schema.`
        : `Proposal marked ${result.status.replace(/_/g, ' ')}.`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Review failed')
    } finally { setBusyId(null) }
  }

  const edit = async (
    proposal: SchemaProposal,
    draft: Parameters<typeof editSchemaProposal>[1],
  ) => {
    setBusyId(proposal.proposal_id)
    setError(null)
    try {
      const result = await editSchemaProposal(proposal.proposal_id, draft)
      setNotice(result.validation_errors.length
        ? `Revision ${result.revision_number} saved with validation issues.`
        : `Revision ${result.revision_number} saved and validated.`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save proposal revision')
    } finally { setBusyId(null) }
  }

  const runMaintenanceBatch = async () => {
    setError(null)
    setBatchRunning(true)
    try {
      const result = await runPendingRecanonicalizations()
      setNotice(`Global recanonicalization batch ${result.job_id.slice(0, 8)} started for ${maintenanceTasks.length} project workflow(s).`)
      maintenancePollRef.current?.cancel()
      maintenancePollRef.current = startJobPolling({
        jobId: result.job_id,
        onComplete: job => {
          setBatchRunning(false)
          const completed = Number(job.result?.completed ?? 0)
          const failed = Number(job.result?.failed ?? 0)
          setNotice(`Global recanonicalization batch finished: ${completed} completed, ${failed} failed.`)
          load()
        },
        onFailed: job => {
          setBatchRunning(false)
          setError(job?.error ?? 'Global recanonicalization batch failed')
        },
      })
    } catch (err) {
      setBatchRunning(false)
      setError(err instanceof Error ? err.message : 'Could not start recanonicalization')
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-violet-700/70 bg-violet-950/25 p-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-violet-200">Workflow Schema Curator</h3>
              <span className="rounded-full border border-violet-600 bg-violet-900/60 px-2 py-0.5 text-[11px] uppercase tracking-wide text-violet-200">Global</span>
              <span className="rounded-full border border-sky-700 bg-sky-950/50 px-2 py-0.5 text-[11px] text-sky-300">LLM-assisted</span>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">
              Deterministic discovery finds corpus-level signals; an LLM curator evaluates their semantics against workflow evidence. Humans can edit drafts, request revisions, and retain final approval authority.
            </p>
          </div>
          <div className="flex items-end gap-2">
            <label className="text-xs text-slate-400">Minimum support
              <input type="number" min={1} value={minSupport}
                onChange={event => setMinSupport(Math.max(1, Number(event.target.value) || 1))}
                className="mt-1 block w-24 rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm text-slate-200" />
            </label>
            <button onClick={runCurator} disabled={curating}
              className="rounded bg-violet-700 px-4 py-2 text-sm text-white hover:bg-violet-600 disabled:opacity-40">
              {curating ? 'Agent analyzing…' : 'Run LLM curator'}
            </button>
          </div>
        </div>
      </div>

      {schema && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="rounded-lg border border-slate-700 bg-slate-800 p-3 sm:col-span-2">
          <p className="text-xs uppercase tracking-wide text-slate-500">Active schema</p>
          <p className="mt-1 text-lg font-semibold text-teal-300">{schema.schema_version}</p>
          <p className="mt-1 text-xs text-slate-400">{schema.change_summary}</p>
        </div>
        {[['Object schemas', schema.object_schema_count], ['Operation templates', schema.operation_template_count], ['Queued recanonicalizations', schema.pending_recanonicalizations]].map(([label, value]) => (
          <div key={String(label)} className="rounded-lg border border-slate-700 bg-slate-800 p-3">
            <p className="text-xs text-slate-500">{label}</p>
            <p className="mt-1 text-xl font-semibold">{value}</p>
          </div>
        ))}
      </div>}

      {maintenanceTasks.length > 0 && <div className="rounded-lg border border-amber-800/70 bg-amber-950/15 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-amber-200">Global recanonicalization batch</p>
            <p className="mt-0.5 text-xs text-slate-400">One batch covering {maintenanceTasks.length} project workflow(s), all targeting their newest queued schema.</p>
          </div>
          <button onClick={runMaintenanceBatch} disabled={batchRunning}
            className="rounded border border-amber-700 bg-amber-950/40 px-3 py-1.5 text-sm text-amber-200 disabled:opacity-40">
            {batchRunning ? 'Batch running…' : 'Run one global batch'}
          </button>
        </div>
        <details className="mt-3 border-t border-amber-900/50 pt-2">
          <summary className="cursor-pointer text-xs text-slate-500">Show project-level audit tasks</summary>
          <div className="mt-2 space-y-2">{maintenanceTasks.map(task => <div key={task.task_id}
            className="rounded bg-slate-900/70 px-3 py-2 text-xs">
            <p className="text-slate-300">Project <span className="font-mono">{task.project_id}</span></p>
            <p className="text-slate-500">{task.reason.replace(/_/g, ' ')} → {task.target_schema_version ?? 'active schema'}</p>
          </div>)}</div>
        </details>
      </div>}

      {error && <div className="rounded border border-red-700 bg-red-950/40 px-3 py-2 text-sm text-red-200">{error}</div>}
      {notice && <div className="rounded border border-teal-700 bg-teal-950/40 px-3 py-2 text-sm text-teal-200">{notice}</div>}

      <div className="flex items-end justify-between gap-4 flex-wrap border-b border-slate-700">
        <div className="flex gap-1 flex-wrap">{FILTERS.map(item => <button key={item.key} onClick={() => setFilter(item.key)}
          className={`px-3 py-2 text-sm border-b-2 -mb-px ${filter === item.key ? 'border-teal-500 text-teal-300' : 'border-transparent text-slate-400'}`}>
          {item.label}{item.key !== 'all' && schema?.proposal_counts[item.key] != null ? ` (${schema.proposal_counts[item.key]})` : ''}
        </button>)}</div>
        <label className="pb-2 text-xs text-slate-400">Reviewer
          <input value={reviewer} onChange={event => setReviewer(event.target.value)}
            className="ml-2 rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-200" />
        </label>
      </div>

      {loading ? <p className="py-8 text-center text-sm text-slate-400">Loading global schema proposals…</p>
        : proposals.length === 0 ? <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center">
          <p className="text-slate-300">No {filter === 'all' ? '' : `${filter.replace(/_/g, ' ')} `}schema proposals.</p>
          <p className="mt-1 text-sm text-slate-500">Run the LLM curator after canonical workflows have accumulated.</p>
        </div>
          : <div className="grid gap-3 lg:grid-cols-2">{proposals.map(proposal => <ProposalCard
            key={proposal.proposal_id} proposal={proposal} reviewer={reviewer}
            busy={busyId === proposal.proposal_id} onReview={review} onEdit={edit} />)}</div>}
    </div>
  )
}
