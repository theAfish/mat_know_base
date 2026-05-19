import { useState } from 'react'
import { createSpace, updateSpace } from '../api/spaces'
import type { Space, SpaceCreatePayload } from '../types'

export interface SpaceDraft {
  name: string
  domain: string
  purpose: string
  description?: string
  extraction_schema: Record<string, unknown>
  system_prompt: string
  field_descriptions: Record<string, unknown>
}

interface Props {
  draft: SpaceDraft
  existing?: Space | null
  onSaved?: (result: { space_id: string; name: string }) => void
}

const PURPOSE_COLORS: Record<string, string> = {
  tabular_database: 'bg-teal-700 text-teal-100',
  qa_benchmark: 'bg-indigo-700 text-indigo-100',
  skill_cards: 'bg-amber-700 text-amber-100',
  freeform: 'bg-slate-600 text-slate-100',
}

export default function SpaceDraftCard({ draft, existing, onSaved }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAs, setSavedAs] = useState<string | null>(null)

  const purposeClass = PURPOSE_COLORS[draft.purpose] ?? 'bg-slate-600 text-slate-100'

  const handleSave = async (mode: 'create' | 'update') => {
    setBusy(true)
    setError(null)
    try {
      if (mode === 'create') {
        const payload: SpaceCreatePayload = {
          name: draft.name,
          domain: draft.domain,
          purpose: draft.purpose,
          description: draft.description ?? '',
          extraction_schema: draft.extraction_schema,
          system_prompt: draft.system_prompt,
          field_descriptions: draft.field_descriptions,
        }
        const res = await createSpace(payload)
        if ((res as unknown as { error?: string }).error) {
          throw new Error((res as unknown as { error: string }).error)
        }
        setSavedAs(res.space_id)
        onSaved?.({ space_id: res.space_id, name: res.name })
      } else if (existing) {
        const res = await updateSpace(existing.space_id, {
          domain: draft.domain,
          purpose: draft.purpose,
          description: draft.description ?? '',
          extraction_schema: draft.extraction_schema,
          system_prompt: draft.system_prompt,
          field_descriptions: draft.field_descriptions,
        })
        setSavedAs(existing.space_id)
        onSaved?.({ space_id: existing.space_id, name: `${existing.name} (v${res.version})` })
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  if (savedAs) {
    return (
      <div className="my-2 px-3 py-2 rounded-md bg-emerald-900/40 border border-emerald-700 text-xs text-emerald-200">
        ✓ Saved space <span className="font-mono">{draft.name}</span> ({savedAs.slice(0, 8)}…)
      </div>
    )
  }

  return (
    <div className="my-2 rounded-md border border-slate-600 bg-slate-900/60 text-xs overflow-hidden">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-slate-700">
        <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${purposeClass}`}>
          {draft.purpose.replace('_', ' ')}
        </span>
        <span className="font-mono text-slate-200">{draft.name}</span>
        <span className="text-slate-500">·</span>
        <span className="text-slate-400 truncate">{draft.domain}</span>
        {existing && (
          <span className="ml-auto text-amber-400 text-[10px]">existing v{existing.version}</span>
        )}
      </div>

      {draft.description && (
        <p className="px-3 py-1.5 text-slate-400 border-b border-slate-700">{draft.description}</p>
      )}

      <button
        className="w-full px-3 py-1.5 text-left text-slate-400 hover:bg-slate-800/50 border-b border-slate-700"
        onClick={() => setExpanded(s => !s)}
      >
        {expanded ? '▾ Hide schema JSON' : '▸ Show schema JSON'}
      </button>

      {expanded && (
        <pre className="px-3 py-2 max-h-96 overflow-auto bg-slate-950/80 text-slate-300 text-[11px] leading-snug font-mono">
{JSON.stringify(draft, null, 2)}
        </pre>
      )}

      {error && (
        <div className="px-3 py-2 text-rose-300 bg-rose-900/30 border-t border-rose-800">{error}</div>
      )}

      <div className="px-3 py-2 flex flex-wrap gap-2 bg-slate-800/40">
        {existing ? (
          <>
            <button
              disabled={busy}
              onClick={() => handleSave('update')}
              className="px-3 py-1 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white rounded text-xs font-medium"
            >
              {busy ? 'Saving…' : `Update "${existing.name}"`}
            </button>
            <button
              disabled={busy}
              onClick={() => handleSave('create')}
              className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 rounded text-xs"
            >
              Save as new
            </button>
          </>
        ) : (
          <button
            disabled={busy}
            onClick={() => handleSave('create')}
            className="px-3 py-1 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-xs font-medium"
          >
            {busy ? 'Saving…' : 'Save as new space'}
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * Try to extract one or more space drafts from a markdown chunk by looking
 * for ```json fenced blocks whose parsed object contains at least
 * `name` and `extraction_schema`.
 */
export function extractDraftsFromText(text: string): SpaceDraft[] {
  const drafts: SpaceDraft[] = []
  const fenceRe = /```(?:json)?\s*\n([\s\S]*?)```/gi
  let match: RegExpExecArray | null
  while ((match = fenceRe.exec(text)) !== null) {
    const body = match[1].trim()
    try {
      const obj = JSON.parse(body)
      if (
        obj &&
        typeof obj === 'object' &&
        typeof obj.name === 'string' &&
        typeof obj.extraction_schema === 'object' &&
        obj.extraction_schema !== null
      ) {
        drafts.push({
          name: obj.name,
          domain: obj.domain ?? '',
          purpose: obj.purpose ?? 'tabular_database',
          description: obj.description ?? '',
          extraction_schema: obj.extraction_schema,
          system_prompt: obj.system_prompt ?? '',
          field_descriptions: obj.field_descriptions ?? {},
        })
      }
    } catch {
      // not JSON, ignore
    }
  }
  return drafts
}
