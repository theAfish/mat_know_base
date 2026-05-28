import { useEffect, useRef, useState } from 'react'
import { listSpaces, getSpace, createSpace, updateSpace, deleteSpace } from '../api/spaces'
import type { Space, SpaceCreatePayload } from '../types'

const PURPOSE_OPTIONS = ['tabular_database', 'qa_benchmark', 'skill_cards', 'freeform'] as const

const PURPOSE_COLORS: Record<string, string> = {
  tabular_database: 'bg-teal-700 text-teal-100',
  qa_benchmark: 'bg-indigo-700 text-indigo-100',
  skill_cards: 'bg-amber-700 text-amber-100',
  freeform: 'bg-slate-600 text-slate-100',
}

interface EditorState {
  mode: 'create' | 'edit' | 'import'
  space?: Space
  jsonText: string
}

const EMPTY_DRAFT = {
  name: 'my_new_space',
  domain: '',
  purpose: 'tabular_database',
  description: '',
  extraction_schema: {},
  system_prompt: '',
  field_descriptions: {},
}

export default function SpacesPage() {
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selected, setSelected] = useState<Space | null>(null)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const importFileRef = useRef<HTMLInputElement>(null)

  const refresh = async () => {
    try {
      const list = await listSpaces()
      setSpaces(list)
      if (selected) {
        const fresh = list.find(s => s.space_id === selected.space_id) ?? null
        if (fresh) {
          // pull full detail
          const full = await getSpace(fresh.space_id)
          setSelected(full)
        } else {
          setSelected(null)
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => { refresh() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const openSpace = async (s: Space) => {
    setError(null); setInfo(null)
    try {
      const full = await getSpace(s.space_id)
      setSelected(full)
      setEditor(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const openEditor = (state: EditorState) => {
    setEditor(state); setError(null); setInfo(null)
  }

  const handleSave = async () => {
    if (!editor) return
    setBusy(true); setError(null); setInfo(null)
    try {
      const obj = JSON.parse(editor.jsonText)
      if (!obj.name || !obj.extraction_schema) {
        throw new Error('JSON must include at least `name` and `extraction_schema`.')
      }
      if (editor.mode === 'edit' && editor.space) {
        const res = await updateSpace(editor.space.space_id, {
          name: obj.name,
          domain: obj.domain,
          purpose: obj.purpose,
          description: obj.description,
          extraction_schema: obj.extraction_schema,
          system_prompt: obj.system_prompt,
          field_descriptions: obj.field_descriptions,
        })
        setInfo(`Updated. New version: ${res.version}`)
      } else {
        const payload: SpaceCreatePayload = {
          name: obj.name,
          domain: obj.domain ?? '',
          purpose: obj.purpose ?? 'tabular_database',
          description: obj.description ?? '',
          extraction_schema: obj.extraction_schema,
          system_prompt: obj.system_prompt ?? '',
          field_descriptions: obj.field_descriptions ?? {},
        }
        const res = await createSpace(payload)
        setInfo(`Created space ${res.name} (${res.space_id.slice(0, 8)}…)`)
      }
      setEditor(null)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (s: Space) => {
    if (!confirm(`Delete space "${s.name}"? This cannot be undone.`)) return
    setBusy(true); setError(null); setInfo(null)
    try {
      await deleteSpace(s.space_id)
      setInfo(`Deleted "${s.name}".`)
      if (selected?.space_id === s.space_id) setSelected(null)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700 flex-shrink-0">
        <div>
          <h2 className="text-xl font-semibold">Spaces</h2>
          <p className="text-sm text-slate-400">
            Projection schemas for tabular DB, QA benchmarks, skill cards, or freeform extraction.
            Create one with the Assistant, or edit raw JSON here.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() =>
              openEditor({
                mode: 'create',
                jsonText: JSON.stringify(EMPTY_DRAFT, null, 2),
              })
            }
            className="px-3 py-1.5 bg-teal-600 hover:bg-teal-500 text-white rounded text-sm transition-colors"
          >
            + New space
          </button>
          <button
            onClick={() => importFileRef.current?.click()}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-sm transition-colors"
          >
            Import JSON
          </button>
          <input
            ref={importFileRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={e => {
              const file = e.target.files?.[0]
              if (!file) return
              const reader = new FileReader()
              reader.onload = evt => {
                const text = evt.target?.result as string
                openEditor({ mode: 'import', jsonText: text })
              }
              reader.readAsText(file)
              // reset so the same file can be re-selected
              e.target.value = ''
            }}
          />
        </div>
      </div>

      {(error || info) && (
        <div className="px-6 py-2 flex-shrink-0">
          {error && (
            <div className="px-3 py-2 text-sm rounded bg-rose-900/30 border border-rose-700 text-rose-200">
              {error}
            </div>
          )}
          {info && !error && (
            <div className="px-3 py-2 text-sm rounded bg-emerald-900/30 border border-emerald-700 text-emerald-200">
              {info}
            </div>
          )}
        </div>
      )}

      <div className="flex-1 grid grid-cols-12 gap-4 px-6 py-4 overflow-hidden">
        {/* List */}
        <div className="col-span-4 overflow-y-auto space-y-1.5">
          {spaces.length === 0 && (
            <p className="text-slate-500 text-sm text-center mt-12">
              No spaces yet. Ask the Assistant to design one with you, or click "+ New space".
            </p>
          )}
          {spaces.map(s => {
            const purpose = s.purpose ?? 'tabular_database'
            const isActive = selected?.space_id === s.space_id
            return (
              <button
                key={s.space_id}
                onClick={() => openSpace(s)}
                className={`w-full text-left px-3 py-2 rounded border transition-colors ${
                  isActive
                    ? 'bg-slate-800 border-teal-500'
                    : 'bg-slate-800/50 border-slate-700 hover:bg-slate-800'
                }`}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${
                      PURPOSE_COLORS[purpose] ?? 'bg-slate-600 text-slate-100'
                    }`}
                  >
                    {purpose.replace('_', ' ')}
                  </span>
                  <span className="font-mono text-sm text-slate-200 truncate">{s.name}</span>
                  {s.version != null && (
                    <span className="ml-auto text-xs text-slate-500">v{s.version}</span>
                  )}
                </div>
                <div className="text-xs text-slate-400 truncate">{s.domain}</div>
                {s.description && (
                  <div className="text-xs text-slate-500 truncate mt-0.5">{s.description}</div>
                )}
              </button>
            )
          })}
        </div>

        {/* Detail / editor */}
        <div className="col-span-8 overflow-y-auto">
          {editor ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-200">
                  {editor.mode === 'edit'
                    ? `Edit ${editor.space?.name}`
                    : editor.mode === 'import'
                    ? 'Import space JSON'
                    : 'New space'}
                </h3>
                <span className="text-xs text-slate-500">
                  Edit the full JSON below.
                </span>
                <div className="ml-auto flex gap-2">
                  <button
                    disabled={busy}
                    onClick={handleSave}
                    className="px-3 py-1 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-xs font-medium"
                  >
                    {busy ? 'Saving…' : editor.mode === 'edit' ? 'Save changes' : 'Create space'}
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => setEditor(null)}
                    className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 rounded text-xs"
                  >
                    Cancel
                  </button>
                </div>
              </div>
              <textarea
                value={editor.jsonText}
                onChange={e => setEditor({ ...editor, jsonText: e.target.value })}
                spellCheck={false}
                className="w-full h-[70vh] bg-slate-950 border border-slate-700 rounded-md p-3 text-xs font-mono text-slate-200 focus:outline-none focus:border-teal-500"
                placeholder="Paste or write a space JSON here…"
              />
              <p className="text-xs text-slate-500">
                Required keys: <code>name</code>, <code>extraction_schema</code>. Recommended:{' '}
                <code>domain</code>, <code>purpose</code> (one of {PURPOSE_OPTIONS.join(', ')}),{' '}
                <code>system_prompt</code>, <code>field_descriptions</code>.
              </p>
            </div>
          ) : selected ? (
            <SpaceDetail
              space={selected}
              onEdit={() =>
                openEditor({
                  mode: 'edit',
                  space: selected,
                  jsonText: JSON.stringify(
                    {
                      name: selected.name,
                      domain: selected.domain,
                      purpose: selected.purpose ?? 'tabular_database',
                      description: selected.description,
                      extraction_schema: selected.extraction_schema,
                      system_prompt: selected.system_prompt ?? '',
                      field_descriptions: selected.field_descriptions ?? {},
                    },
                    null,
                    2,
                  ),
                })
              }
              onDelete={() => handleDelete(selected)}
            />
          ) : (
            <p className="text-slate-500 text-sm text-center mt-12">
              Select a space to view its full definition.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function SpaceDetail({
  space,
  onEdit,
  onDelete,
}: {
  space: Space
  onEdit: () => void
  onDelete: () => void
}) {
  const purpose = space.purpose ?? 'tabular_database'
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${
            PURPOSE_COLORS[purpose] ?? 'bg-slate-600 text-slate-100'
          }`}
        >
          {purpose.replace('_', ' ')}
        </span>
        <h3 className="font-mono text-slate-100">{space.name}</h3>
        <span className="text-slate-500 text-xs">v{space.version}</span>
        <span className="text-slate-500 text-xs">·</span>
        <span className="text-slate-400 text-xs">{space.domain}</span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={onEdit}
            className="px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded text-xs"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="px-3 py-1 bg-rose-700 hover:bg-rose-600 text-white rounded text-xs"
          >
            Delete
          </button>
        </div>
      </div>

      {space.description && (
        <p className="text-slate-300 bg-slate-800/50 px-3 py-2 rounded">{space.description}</p>
      )}

      <Section title="Extraction schema">
        <pre className="bg-slate-950 text-slate-200 text-xs font-mono p-3 rounded max-h-[40vh] overflow-auto">
{JSON.stringify(space.extraction_schema, null, 2)}
        </pre>
      </Section>

      {space.system_prompt && (
        <Section title="System prompt">
          <pre className="bg-slate-950 text-slate-200 text-xs font-mono p-3 rounded max-h-[30vh] overflow-auto whitespace-pre-wrap">
{String(space.system_prompt)}
          </pre>
        </Section>
      )}

      {space.field_descriptions && (
        <Section title="Field descriptions">
          <pre className="bg-slate-950 text-slate-200 text-xs font-mono p-3 rounded max-h-[30vh] overflow-auto">
{JSON.stringify(space.field_descriptions, null, 2)}
          </pre>
        </Section>
      )}

      <div className="text-xs text-slate-500">
        Created: {space.created_at ?? '—'} · Updated: {space.updated_at ?? '—'}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <div>
      <button
        onClick={() => setOpen(s => !s)}
        className="text-xs text-slate-300 mb-1 hover:text-slate-100"
      >
        {open ? '▾' : '▸'} {title}
      </button>
      {open && children}
    </div>
  )
}
