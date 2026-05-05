import { useState, useCallback, useEffect } from 'react'
import { listFeedback, resolveFeedback, reviewFeedback } from '../api/feedback'
import { getJob } from '../api/jobs'
import StatusBadge from '../components/StatusBadge'
import JobProgress from '../components/JobProgress'
import type { FeedbackItem, Job } from '../types'

const STATUS_OPTIONS = ['all', 'OPEN', 'RESOLVED', 'DISMISSED']

// ─── Resolve modal ────────────────────────────────────────────────────────────

function ResolveModal({
  item,
  onClose,
  onDone,
}: {
  item: FeedbackItem
  onClose: () => void
  onDone: () => void
}) {
  const [resolution, setResolution] = useState<'RESOLVED' | 'DISMISSED'>('RESOLVED')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    try {
      await resolveFeedback(item.feedback_id, resolution, notes)
      onDone()
    } catch {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-lg w-full max-w-lg shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h3 className="font-semibold text-slate-100">Resolve Feedback</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200 text-lg leading-none">×</button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Item summary */}
          <div className="bg-slate-900 rounded-lg p-3 space-y-1">
            <p className="text-xs text-slate-400">
              <span className="text-slate-500">Category:</span> {item.category}
              {item.field_path && <><span className="mx-2 text-slate-600">·</span><span className="text-slate-500">Field:</span> {item.field_path}</>}
            </p>
            <p className="text-sm text-slate-300 mt-1">{item.question}</p>
            {item.context && (
              <p className="text-xs text-slate-500 italic">{item.context.slice(0, 200)}{item.context.length > 200 ? '…' : ''}</p>
            )}
          </div>

          {/* Resolution toggle */}
          <div>
            <label className="block text-xs text-slate-400 mb-2">Resolution</label>
            <div className="flex gap-2">
              {(['RESOLVED', 'DISMISSED'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setResolution(s)}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                    resolution === s
                      ? s === 'RESOLVED'
                        ? 'bg-green-700 text-white'
                        : 'bg-slate-600 text-slate-200'
                      : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">Notes (optional)</label>
            <textarea
              value={notes}
              onChange={e => setNotes(e.target.value)}
              rows={3}
              placeholder="Add resolution notes…"
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm text-slate-200 resize-none focus:outline-none focus:border-teal-500"
            />
          </div>
        </div>

        <div className="px-5 py-3 border-t border-slate-700 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-sm"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving}
            className="px-4 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm font-medium"
          >
            {saving ? 'Saving…' : 'Submit'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function FeedbackPage() {
  const [items, setItems] = useState<FeedbackItem[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('OPEN')
  const [resolveTarget, setResolveTarget] = useState<FeedbackItem | null>(null)
  const [reviewJob, setReviewJob] = useState<Job | null>(null)
  const [isReviewing, setIsReviewing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listFeedback({ status: statusFilter === 'all' ? undefined : statusFilter, limit: 200 })
      setItems(result)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => { load() }, [load])

  const startReview = async () => {
    try {
      setIsReviewing(true)
      const { job_id } = await reviewFeedback({})
      pollJob(job_id)
    } catch {
      setIsReviewing(false)
    }
  }

  const pollJob = (jobId: string) => {
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        setReviewJob(job)
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          setTimeout(poll, 1000)
        } else {
          setIsReviewing(false)
          if (job.status === 'COMPLETED') load()
        }
      } catch { setTimeout(poll, 2000) }
    }
    setTimeout(poll, 500)
  }

  return (
    <div className="p-6 max-w-5xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Feedback</h2>
          <p className="text-sm text-slate-400">Review and resolve agent-generated feedback items.</p>
        </div>
        <button
          onClick={startReview}
          disabled={isReviewing}
          className="px-3 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm font-medium"
        >
          {isReviewing ? 'Running…' : '▶ Run Feedback Review'}
        </button>
      </div>

      {/* Status filter */}
      <div className="flex items-center gap-2">
        {STATUS_OPTIONS.map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-teal-700 text-white'
                : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
            }`}
          >
            {s}
          </button>
        ))}
        <button onClick={load} className="ml-1 text-xs text-teal-400 hover:text-teal-300">Refresh</button>
      </div>

      {reviewJob && <JobProgress job={reviewJob} />}

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-slate-400 text-sm">No feedback items matching the current filter.</p>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <div
              key={item.feedback_id}
              className="bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 hover:border-slate-600 transition-colors"
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <StatusBadge status={item.status} />
                    <span className="text-xs text-slate-400 bg-slate-700 px-1.5 py-0.5 rounded">
                      {item.category}
                    </span>
                    {item.field_path && (
                      <span className="text-xs text-slate-500 font-mono">{item.field_path}</span>
                    )}
                    <span className="text-xs text-slate-600 ml-auto flex-shrink-0">
                      {item.created_at?.slice(0, 10) ?? ''}
                    </span>
                  </div>
                  <p className="text-sm text-slate-200">{item.question}</p>
                  {item.context && (
                    <p className="text-xs text-slate-500 mt-0.5 italic">
                      {item.context.slice(0, 140)}{item.context.length > 140 ? '…' : ''}
                    </p>
                  )}
                  {item.resolution_notes && (
                    <p className="text-xs text-teal-400 mt-0.5">
                      <span className="text-slate-500">Notes: </span>{item.resolution_notes.slice(0, 100)}
                      {item.resolution_notes.length > 100 ? '…' : ''}
                    </p>
                  )}
                </div>

                {item.status === 'OPEN' && (
                  <button
                    onClick={() => setResolveTarget(item)}
                    className="flex-shrink-0 px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs"
                  >
                    Resolve
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {resolveTarget && (
        <ResolveModal
          item={resolveTarget}
          onClose={() => setResolveTarget(null)}
          onDone={() => { setResolveTarget(null); load() }}
        />
      )}
    </div>
  )
}
