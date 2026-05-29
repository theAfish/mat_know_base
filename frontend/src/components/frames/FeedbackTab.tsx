import { useCallback, useEffect, useState } from 'react'

import { listFeedback, resolveFeedback } from '../../api/feedback'
import type { FeedbackItem } from '../../types'
import StatusBadge from '../StatusBadge'


export default function FeedbackTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<FeedbackItem[]>([])
  const [loading, setLoading] = useState(true)
  const [resolvingId, setResolvingId] = useState<string | null>(null)
  const [resolution, setResolution] = useState<'RESOLVED' | 'DISMISSED'>('RESOLVED')
  const [notes, setNotes] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    listFeedback({ project_id: projectId, limit: 100 })
      .then(setItems).catch(() => {}).finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load() }, [load])

  const submit = async (id: string) => {
    await resolveFeedback(id, resolution, notes)
    setResolvingId(null); setNotes(''); load()
  }

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (items.length === 0) return <p className="text-sm text-slate-400">No feedback items for this project.</p>

  return (
    <div className="space-y-2">
      {items.map(item => (
        <div key={item.feedback_id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5">
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <StatusBadge status={item.status} />
                <span className="text-xs text-slate-400 bg-slate-700 px-1.5 py-0.5 rounded">{item.category}</span>
                {item.field_path && <span className="text-xs text-slate-500 font-mono">{item.field_path}</span>}
              </div>
              <p className="text-sm text-slate-300">{item.question}</p>
              {item.context && <p className="text-xs text-slate-500 mt-0.5 italic">{item.context.slice(0, 120)}…</p>}
              {item.resolution_notes && <p className="text-xs text-teal-400 mt-0.5">{item.resolution_notes}</p>}
            </div>
            {item.status === 'OPEN' && resolvingId !== item.feedback_id && (
              <button onClick={() => setResolvingId(item.feedback_id)}
                className="flex-shrink-0 px-2 py-1 bg-slate-700 hover:bg-slate-600 text-xs text-slate-300 rounded">
                Resolve
              </button>
            )}
          </div>
          {resolvingId === item.feedback_id && (
            <div className="mt-2 space-y-2 border-t border-slate-700 pt-2">
              <div className="flex gap-2">
                {(['RESOLVED', 'DISMISSED'] as const).map(s => (
                  <button key={s} onClick={() => setResolution(s)}
                    className={`px-2 py-0.5 rounded text-xs font-medium ${resolution === s ? 'bg-teal-700 text-white' : 'bg-slate-700 text-slate-400'}`}>
                    {s}
                  </button>
                ))}
              </div>
              <textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Resolution notes…" rows={2}
                className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 resize-none focus:outline-none focus:border-teal-500" />
              <div className="flex gap-2">
                <button onClick={() => submit(item.feedback_id)} className="px-3 py-1 bg-teal-600 hover:bg-teal-500 text-white rounded text-xs">Save</button>
                <button onClick={() => setResolvingId(null)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs">Cancel</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
