import { useEffect, useRef, useState } from 'react'
import {
  listSpaces,
  getSpace,
  createSpace,
  updateSpace,
  deleteSpace,
  getDefaultReviewPrompt,
} from '../api/spaces'
import type { PostProcessorProfile, Space, SpaceCreatePayload } from '../types'

const PURPOSE_OPTIONS = ['tabular_database', 'qa_benchmark', 'skill_cards', 'freeform'] as const
const REVIEW_SEARCH_TOOL_OPTIONS = ['web', 'uniprot', 'crossref'] as const
const POST_PROCESSOR_TOOL_OPTIONS = ['reading', 'web', 'uniprot', 'crossref'] as const

const PURPOSE_COLORS: Record<string, string> = {
  tabular_database: 'bg-teal-700 text-teal-100',
  qa_benchmark: 'bg-indigo-700 text-indigo-100',
  skill_cards: 'bg-amber-700 text-amber-100',
  freeform: 'bg-slate-600 text-slate-100',
}

interface EditorState {
  mode: 'create' | 'edit' | 'import'
  space?: Space
  draft: SpaceDraft
}

type SchemaField = {
  type?: string
  item_type?: string
  required?: boolean
  description?: string
  [key: string]: unknown
}

type SchemaSection = {
  type?: string
  description?: string
  filter?: Record<string, unknown>
  item_schema?: Record<string, SchemaField>
  [key: string]: unknown
}

type SpaceDraft = {
  name: string
  domain: string
  purpose: string
  description: string
  extraction_schema: Record<string, SchemaSection>
  system_prompt: string
  field_descriptions: Record<string, string>
  review_prompt: string
  review_trackable: boolean
  review_allow_search: boolean
  review_search_tools: string[]
  post_processors: PostProcessorProfile[]
}

const EMPTY_DRAFT: SpaceDraft = {
  name: 'my_new_space',
  domain: '',
  purpose: 'tabular_database',
  description: '',
  extraction_schema: {},
  system_prompt: '',
  field_descriptions: {},
  review_prompt: '',
  review_trackable: true,
  review_allow_search: false,
  review_search_tools: ['web'],
  post_processors: [
    {
      id: 'default',
      name: 'Default reviewer',
      description: 'General projection review and correction.',
      prompt: null,
      tool_groups: ['reading'],
      enabled: true,
    },
  ],
}

const FIELD_TYPE_OPTIONS = ['string', 'number', 'integer', 'boolean', 'list', 'object'] as const

const toRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}

const stringifyFieldDescription = (value: unknown) =>
  typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2)

const normalizeField = (value: unknown): SchemaField => {
  const obj = toRecord(value)
  return {
    ...obj,
    type: typeof obj.type === 'string' ? obj.type : 'string',
    item_type: typeof obj.item_type === 'string' ? obj.item_type : 'string',
    required: typeof obj.required === 'boolean' ? obj.required : false,
    description: typeof obj.description === 'string' ? obj.description : '',
  }
}

const normalizeSchema = (value: unknown): Record<string, SchemaSection> => {
  const schema = toRecord(value)
  return Object.fromEntries(
    Object.entries(schema).map(([sectionKey, rawSection]) => {
      const section = toRecord(rawSection)
      const itemSchema = toRecord(section.item_schema)
      return [
        sectionKey,
        {
          ...section,
          type: typeof section.type === 'string' ? section.type : 'list',
          description: typeof section.description === 'string' ? section.description : '',
          filter: toRecord(section.filter),
          item_schema: Object.fromEntries(
            Object.entries(itemSchema).map(([fieldKey, rawField]) => [
              fieldKey,
              normalizeField(rawField),
            ]),
          ),
        },
      ]
    }),
  )
}

const slugifyProcessorId = (value: string) => {
  const slug = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return slug || 'reviewer'
}

const normalizePostProcessors = (value: unknown): PostProcessorProfile[] => {
  if (!Array.isArray(value) || value.length === 0) return EMPTY_DRAFT.post_processors
  return value
    .filter(item => item && typeof item === 'object' && !Array.isArray(item))
    .map((item, index) => {
      const obj = item as Record<string, unknown>
      const name = typeof obj.name === 'string' && obj.name.trim()
        ? obj.name
        : `Reviewer ${index + 1}`
      const id = typeof obj.id === 'string' && obj.id.trim()
        ? slugifyProcessorId(obj.id)
        : slugifyProcessorId(name)
      const rawTools = Array.isArray(obj.tool_groups) ? obj.tool_groups : []
      const toolGroups = rawTools
        .map(String)
        .filter(tool => POST_PROCESSOR_TOOL_OPTIONS.includes(tool as (typeof POST_PROCESSOR_TOOL_OPTIONS)[number]))
      return {
        id,
        name,
        description: typeof obj.description === 'string' ? obj.description : '',
        prompt: typeof obj.prompt === 'string' && obj.prompt.trim() ? obj.prompt : null,
        tool_groups: toolGroups.length > 0 ? Array.from(new Set(toolGroups)) : ['reading'],
        enabled: typeof obj.enabled === 'boolean' ? obj.enabled : true,
      }
    })
}

const draftFromObject = (obj: Record<string, unknown>): SpaceDraft => ({
  name: typeof obj.name === 'string' ? obj.name : EMPTY_DRAFT.name,
  domain: typeof obj.domain === 'string' ? obj.domain : '',
  purpose: typeof obj.purpose === 'string' ? obj.purpose : 'tabular_database',
  description: typeof obj.description === 'string' ? obj.description : '',
  extraction_schema: normalizeSchema(obj.extraction_schema),
  system_prompt: typeof obj.system_prompt === 'string' ? obj.system_prompt : '',
  field_descriptions: Object.fromEntries(
    Object.entries(toRecord(obj.field_descriptions)).map(([key, value]) => [
      key,
      stringifyFieldDescription(value),
    ]),
  ),
  review_prompt: typeof obj.review_prompt === 'string' ? obj.review_prompt : '',
  review_trackable: typeof obj.review_trackable === 'boolean' ? obj.review_trackable : true,
  review_allow_search:
    typeof obj.review_allow_search === 'boolean' ? obj.review_allow_search : false,
  review_search_tools: Array.isArray(obj.review_search_tools)
    ? obj.review_search_tools.map(String)
    : ['web'],
  post_processors: normalizePostProcessors(obj.post_processors),
})

const draftFromSpace = (space: Space, reviewPrompt?: string): SpaceDraft =>
  draftFromObject({
    name: space.name,
    domain: space.domain,
    purpose: space.purpose ?? 'tabular_database',
    description: space.description,
    extraction_schema: space.extraction_schema,
    system_prompt: space.system_prompt ?? '',
    field_descriptions: space.field_descriptions ?? {},
    review_prompt: reviewPrompt ?? space.review_prompt ?? '',
    review_trackable: space.review_trackable ?? true,
    review_allow_search: space.review_allow_search ?? false,
    review_search_tools: space.review_search_tools ?? ['web'],
    post_processors: space.post_processors,
  })

const draftToPayload = (draft: SpaceDraft): SpaceCreatePayload => ({
  name: draft.name.trim(),
  domain: draft.domain.trim(),
  purpose: draft.purpose,
  description: draft.description,
  extraction_schema: draft.extraction_schema,
  system_prompt: draft.system_prompt,
  field_descriptions: draft.field_descriptions,
  review_prompt: draft.review_prompt.trim().length > 0 ? draft.review_prompt : null,
  review_trackable: draft.review_trackable,
  review_allow_search: draft.review_allow_search,
  review_search_tools: draft.review_search_tools.length > 0 ? draft.review_search_tools : ['web'],
  post_processors: draft.post_processors,
})

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
      const payload = draftToPayload(editor.draft)
      if (!payload.name || !payload.extraction_schema) {
        throw new Error('Space must include at least a name and extraction schema.')
      }
      if (editor.mode === 'edit' && editor.space) {
        const res = await updateSpace(editor.space.space_id, payload)
        setInfo(`Updated. New version: ${res.version}`)
      } else {
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
            Create one with the Assistant, import JSON, or tune it with structured controls.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() =>
              openEditor({
                mode: 'create',
                draft: EMPTY_DRAFT,
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
                try {
                  openEditor({ mode: 'import', draft: draftFromObject(JSON.parse(text)) })
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err))
                }
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
                  Edit with normal fields; long prompts use real line breaks.
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
              <SpaceForm
                draft={editor.draft}
                onChange={draft => setEditor({ ...editor, draft })}
              />
            </div>
          ) : selected ? (
            <SpaceDetail
              space={selected}
              onEdit={() =>
                openEditor({
                  mode: 'edit',
                  space: selected,
                  draft: draftFromSpace(selected),
                })
              }
              onDelete={() => handleDelete(selected)}
              onCustomizeReview={defaultPrompt =>
                openEditor({
                  mode: 'edit',
                  space: selected,
                  draft: draftFromSpace(selected, defaultPrompt),
                })
              }
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

function SpaceForm({
  draft,
  onChange,
}: {
  draft: SpaceDraft
  onChange: (draft: SpaceDraft) => void
}) {
  const setDraft = (patch: Partial<SpaceDraft>) => onChange({ ...draft, ...patch })
  const schemaEntries = Object.entries(draft.extraction_schema)

  const updateSection = (sectionKey: string, patch: Partial<SchemaSection>) => {
    setDraft({
      extraction_schema: {
        ...draft.extraction_schema,
        [sectionKey]: { ...draft.extraction_schema[sectionKey], ...patch },
      },
    })
  }

  const renameSection = (oldKey: string, newKey: string) => {
    const clean = newKey.trim()
    if (!clean || clean === oldKey || draft.extraction_schema[clean]) return
    const next = Object.fromEntries(
      Object.entries(draft.extraction_schema).map(([key, value]) =>
        key === oldKey ? [clean, value] : [key, value],
      ),
    )
    const { [oldKey]: oldDescription, ...remainingDescriptions } = draft.field_descriptions
    setDraft({
      extraction_schema: next,
      field_descriptions: {
        ...remainingDescriptions,
        [clean]: draft.field_descriptions[clean] ?? oldDescription ?? '',
      },
    })
  }

  const addSection = () => {
    const base = 'new_section'
    let key = base
    let index = 2
    while (draft.extraction_schema[key]) {
      key = `${base}_${index}`
      index += 1
    }
    setDraft({
      extraction_schema: {
        ...draft.extraction_schema,
        [key]: { type: 'list', description: '', filter: {}, item_schema: {} },
      },
      field_descriptions: { ...draft.field_descriptions, [key]: '' },
    })
  }

  const removeSection = (sectionKey: string) => {
    const { [sectionKey]: _removed, ...nextSchema } = draft.extraction_schema
    const { [sectionKey]: _removedDescription, ...nextDescriptions } = draft.field_descriptions
    setDraft({ extraction_schema: nextSchema, field_descriptions: nextDescriptions })
  }

  const updateField = (sectionKey: string, fieldKey: string, patch: Partial<SchemaField>) => {
    const section = draft.extraction_schema[sectionKey]
    updateSection(sectionKey, {
      item_schema: {
        ...(section.item_schema ?? {}),
        [fieldKey]: { ...(section.item_schema?.[fieldKey] ?? {}), ...patch },
      },
    })
  }

  const renameField = (sectionKey: string, oldKey: string, newKey: string) => {
    const clean = newKey.trim()
    const section = draft.extraction_schema[sectionKey]
    const itemSchema = section.item_schema ?? {}
    if (!clean || clean === oldKey || itemSchema[clean]) return
    updateSection(sectionKey, {
      item_schema: Object.fromEntries(
        Object.entries(itemSchema).map(([key, value]) =>
          key === oldKey ? [clean, value] : [key, value],
        ),
      ),
    })
  }

  const addField = (sectionKey: string) => {
    const section = draft.extraction_schema[sectionKey]
    const itemSchema = section.item_schema ?? {}
    const base = 'new_field'
    let key = base
    let index = 2
    while (itemSchema[key]) {
      key = `${base}_${index}`
      index += 1
    }
    updateSection(sectionKey, {
      item_schema: {
        ...itemSchema,
        [key]: { type: 'string', item_type: 'string', required: false, description: '' },
      },
    })
  }

  const removeField = (sectionKey: string, fieldKey: string) => {
    const section = draft.extraction_schema[sectionKey]
    const { [fieldKey]: _removed, ...nextFields } = section.item_schema ?? {}
    updateSection(sectionKey, { item_schema: nextFields })
  }

  return (
    <div className="space-y-4 pb-6">
      <div className="grid grid-cols-2 gap-3">
        <TextInput label="Name" value={draft.name} onChange={name => setDraft({ name })} />
        <TextInput label="Domain" value={draft.domain} onChange={domain => setDraft({ domain })} />
        <label className="text-xs text-slate-300">
          <span className="block mb-1">Purpose</span>
          <select
            value={draft.purpose}
            onChange={e => setDraft({ purpose: e.target.value })}
            className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-sm text-slate-100 focus:outline-none focus:border-teal-500"
          >
            {PURPOSE_OPTIONS.map(option => (
              <option key={option} value={option}>{option.replace('_', ' ')}</option>
            ))}
          </select>
        </label>
        <TextInput
          label="Description"
          value={draft.description}
          onChange={description => setDraft({ description })}
        />
      </div>

      <Section title="Extraction schema">
        <div className="space-y-3">
          {schemaEntries.length === 0 && (
            <p className="text-xs text-slate-500">No schema sections yet.</p>
          )}
          {schemaEntries.map(([sectionKey, section]) => (
            <div key={sectionKey} className="border border-slate-700 bg-slate-900/40 rounded p-3 space-y-3">
              <div className="grid grid-cols-[minmax(0,1fr)_120px_auto] gap-2 items-end">
                <TextInput
                  label="Section"
                  value={sectionKey}
                  onChange={value => renameSection(sectionKey, value)}
                />
                <label className="text-xs text-slate-300">
                  <span className="block mb-1">Shape</span>
                  <select
                    value={section.type ?? 'list'}
                    onChange={e => updateSection(sectionKey, { type: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-sm text-slate-100 focus:outline-none focus:border-teal-500"
                  >
                    <option value="list">list</option>
                    <option value="object">object</option>
                    <option value="string">string</option>
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => removeSection(sectionKey)}
                  className="px-2 py-2 bg-rose-900/60 hover:bg-rose-800 text-rose-100 rounded text-xs"
                >
                  Remove
                </button>
              </div>
              <TextArea
                label="Section description"
                value={section.description ?? ''}
                rows={2}
                onChange={description => updateSection(sectionKey, { description })}
              />
              <TextInput
                label="Field description shown to agents"
                value={draft.field_descriptions[sectionKey] ?? ''}
                onChange={value =>
                  setDraft({
                    field_descriptions: { ...draft.field_descriptions, [sectionKey]: value },
                  })
                }
              />
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-300">Fields</span>
                  <button
                    type="button"
                    onClick={() => addField(sectionKey)}
                    className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs"
                  >
                    + Add field
                  </button>
                </div>
                {(Object.entries(section.item_schema ?? {})).map(([fieldKey, field]) => (
                  <div
                    key={fieldKey}
                    className="grid grid-cols-[minmax(120px,1fr)_110px_90px_minmax(160px,2fr)_auto] gap-2 items-end"
                  >
                    <TextInput
                      label="Key"
                      value={fieldKey}
                      onChange={value => renameField(sectionKey, fieldKey, value)}
                    />
                    <label className="text-xs text-slate-300">
                      <span className="block mb-1">Type</span>
                      <select
                        value={field.type ?? 'string'}
                        onChange={e => updateField(sectionKey, fieldKey, { type: e.target.value })}
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-sm text-slate-100 focus:outline-none focus:border-teal-500"
                      >
                        {FIELD_TYPE_OPTIONS.map(option => (
                          <option key={option} value={option}>{option}</option>
                        ))}
                      </select>
                    </label>
                    <label className="text-xs text-slate-300">
                      <span className="block mb-1">Required</span>
                      <input
                        type="checkbox"
                        checked={field.required ?? false}
                        onChange={e =>
                          updateField(sectionKey, fieldKey, { required: e.target.checked })
                        }
                        className="mt-2 h-4 w-4 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
                      />
                    </label>
                    <TextInput
                      label="Description"
                      value={field.description ?? ''}
                      onChange={description =>
                        updateField(sectionKey, fieldKey, { description })
                      }
                    />
                    <button
                      type="button"
                      onClick={() => removeField(sectionKey, fieldKey)}
                      className="px-2 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={addSection}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs"
          >
            + Add section
          </button>
        </div>
      </Section>

      <Section title="Prompts">
        <div className="space-y-3">
          <TextArea
            label="System prompt"
            value={draft.system_prompt}
            rows={9}
            onChange={system_prompt => setDraft({ system_prompt })}
          />
          <ReviewPromptEditor
            purpose={draft.purpose}
            value={draft.review_prompt}
            onChange={review_prompt => setDraft({ review_prompt })}
          />
        </div>
      </Section>

      <Section title="Review settings">
        <div className="space-y-3 text-xs text-slate-300">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.review_trackable}
              onChange={e => setDraft({ review_trackable: e.target.checked })}
              className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
            />
            Track review history by creating superseding projection rows
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.review_allow_search}
              onChange={e => setDraft({ review_allow_search: e.target.checked })}
              className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
            />
            Allow review search
          </label>
          <div className="flex items-center gap-3 flex-wrap">
            {REVIEW_SEARCH_TOOL_OPTIONS.map(tool => {
              const selected = draft.review_search_tools.includes(tool)
              return (
                <label key={tool} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!draft.review_allow_search}
                    onChange={e => {
                      const next = e.target.checked
                        ? [...draft.review_search_tools, tool]
                        : draft.review_search_tools.filter(value => value !== tool)
                      setDraft({ review_search_tools: Array.from(new Set(next)) })
                    }}
                    className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
                  />
                  {tool}
                </label>
              )
            })}
          </div>
        </div>
      </Section>

      <Section title="Post processors">
        <PostProcessorEditor
          processors={draft.post_processors}
          onChange={post_processors => setDraft({ post_processors })}
        />
      </Section>
    </div>
  )
}

function PostProcessorEditor({
  processors,
  onChange,
}: {
  processors: PostProcessorProfile[]
  onChange: (processors: PostProcessorProfile[]) => void
}) {
  const update = (index: number, patch: Partial<PostProcessorProfile>) => {
    onChange(processors.map((processor, i) => (
      i === index ? { ...processor, ...patch } : processor
    )))
  }
  const remove = (index: number) => {
    if (processors.length <= 1) return
    onChange(processors.filter((_, i) => i !== index))
  }
  const add = () => {
    const nextNumber = processors.length + 1
    const idBase = `reviewer_${nextNumber}`
    let id = idBase
    let suffix = 2
    const ids = new Set(processors.map(processor => processor.id))
    while (ids.has(id)) {
      id = `${idBase}_${suffix}`
      suffix += 1
    }
    onChange([
      ...processors,
      {
        id,
        name: `Reviewer ${nextNumber}`,
        description: '',
        prompt: null,
        tool_groups: ['reading'],
        enabled: true,
      },
    ])
  }

  return (
    <div className="space-y-3 text-xs text-slate-300">
      {processors.map((processor, index) => (
        <div key={`${processor.id}-${index}`} className="rounded border border-slate-700 bg-slate-900/40 p-3 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <TextInput
              label="ID"
              value={processor.id}
              onChange={id => update(index, { id: slugifyProcessorId(id) })}
            />
            <TextInput
              label="Name"
              value={processor.name}
              onChange={name => update(index, { name })}
            />
          </div>
          <TextInput
            label="Description"
            value={processor.description ?? ''}
            onChange={description => update(index, { description })}
          />
          <TextArea
            label="Prompt override"
            value={processor.prompt ?? ''}
            rows={6}
            onChange={prompt => update(index, { prompt: prompt.trim() ? prompt : null })}
          />
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={processor.enabled !== false}
                onChange={e => update(index, { enabled: e.target.checked })}
                className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
              />
              enabled
            </label>
            {POST_PROCESSOR_TOOL_OPTIONS.map(tool => {
              const selected = processor.tool_groups.includes(tool)
              return (
                <label key={tool} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={e => {
                      const next = e.target.checked
                        ? [...processor.tool_groups, tool]
                        : processor.tool_groups.filter(value => value !== tool)
                      update(index, { tool_groups: Array.from(new Set(next)) })
                    }}
                    className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-teal-500 focus:ring-teal-500"
                  />
                  {tool}
                </label>
              )
            })}
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={processors.length <= 1}
              onClick={() => remove(index)}
              className="px-2 py-1 rounded bg-slate-800 text-slate-300 hover:bg-slate-700 disabled:opacity-40"
            >
              Remove
            </button>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs"
      >
        + Add post processor
      </button>
    </div>
  )
}

function TextInput({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="text-xs text-slate-300">
      <span className="block mb-1">{label}</span>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-sm text-slate-100 focus:outline-none focus:border-teal-500"
      />
    </label>
  )
}

function TextArea({
  label,
  value,
  rows,
  onChange,
}: {
  label: string
  value: string
  rows: number
  onChange: (value: string) => void
}) {
  return (
    <label className="text-xs text-slate-300 block">
      <span className="block mb-1">{label}</span>
      <textarea
        value={value}
        rows={rows}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-sm text-slate-100 focus:outline-none focus:border-teal-500"
      />
    </label>
  )
}

function ReviewPromptEditor({
  purpose,
  value,
  onChange,
}: {
  purpose: string
  value: string
  onChange: (value: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleLoadDefault = async () => {
    setBusy(true); setErr(null)
    try {
      const res = await getDefaultReviewPrompt(purpose)
      onChange(res.review_prompt)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <TextArea label="Review prompt" value={value} rows={9} onChange={onChange} />
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={handleLoadDefault}
          className="px-2 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 rounded text-xs"
        >
          {busy ? 'Loading…' : 'Load default review prompt for purpose'}
        </button>
        {err && <span className="text-xs text-rose-300">{err}</span>}
      </div>
    </div>
  )
}

function SpaceDetail({
  space,
  onEdit,
  onDelete,
  onCustomizeReview,
}: {
  space: Space
  onEdit: () => void
  onDelete: () => void
  onCustomizeReview: (defaultPrompt: string) => void
}) {
  const purpose = space.purpose ?? 'tabular_database'
  const schema = normalizeSchema(space.extraction_schema)
  const sectionCount = Object.keys(schema).length
  const fieldCount = Object.values(schema).reduce(
    (sum, section) => sum + Object.keys(section.item_schema ?? {}).length,
    0,
  )
  const searchTools = space.review_search_tools ?? ['web']
  const processors = space.post_processors ?? []
  return (
    <div className="space-y-4 text-sm pb-6">
      <div className="border border-slate-700 bg-slate-900/50 rounded p-4 space-y-3">
        <div className="flex items-start gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${
                  PURPOSE_COLORS[purpose] ?? 'bg-slate-600 text-slate-100'
                }`}
              >
                {purpose.replace('_', ' ')}
              </span>
              {space.version != null && (
                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px]">
                  v{space.version}
                </span>
              )}
            </div>
            <h3 className="mt-2 text-lg font-semibold text-slate-100 break-words">{space.name}</h3>
            <div className="mt-1 text-xs text-slate-400">{space.domain || 'No domain set'}</div>
          </div>
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
          <p className="text-slate-300 leading-relaxed">{space.description}</p>
        )}

        <div className="grid grid-cols-4 gap-2">
          <SummaryTile label="Sections" value={String(sectionCount)} />
          <SummaryTile label="Fields" value={String(fieldCount)} />
          <SummaryTile label="Review history" value={space.review_trackable ?? true ? 'On' : 'Off'} />
          <SummaryTile label="Processors" value={String(processors.length)} />
        </div>

        <div className="text-xs text-slate-500">
          Created: {space.created_at ?? '—'} · Updated: {space.updated_at ?? '—'}
        </div>
      </div>

      <Section title="Extraction schema">
        <SchemaOverview schema={schema} />
      </Section>

      {space.field_descriptions && Object.keys(space.field_descriptions).length > 0 && (
        <Section title="Field descriptions">
          <FieldDescriptionsView descriptions={space.field_descriptions} />
        </Section>
      )}

      {space.system_prompt && (
        <Section title="System prompt">
          <PromptBlock text={space.system_prompt} />
        </Section>
      )}

      <Section title="Review prompt">
        <ReviewPromptView
          custom={space.review_prompt ?? null}
          purpose={purpose}
          onUseAsCustom={onCustomizeReview}
        />
      </Section>

      <Section title="Review settings">
        <div className="grid grid-cols-2 gap-3">
          <SettingPanel
            label="Review history"
            enabled={space.review_trackable ?? true}
            enabledText="Creates superseding projection rows"
            disabledText="Uses legacy in-place updates"
          />
          <div className="border border-slate-700 bg-slate-900/40 rounded p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-slate-300">Review search</span>
              <StatusPill enabled={space.review_allow_search ?? false} />
            </div>
            <div className="mt-2 flex gap-1.5 flex-wrap">
              {searchTools.map(tool => (
                <span
                  key={tool}
                  className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px]"
                >
                  {tool}
                </span>
              ))}
            </div>
          </div>
        </div>
      </Section>

      <Section title="Post processors">
        <div className="space-y-2">
          {processors.map(processor => (
            <div key={processor.id} className="border border-slate-700 bg-slate-900/40 rounded p-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="text-sm font-medium text-slate-200">{processor.name}</div>
                  <div className="text-[11px] text-slate-500">{processor.id}</div>
                </div>
                <StatusPill enabled={processor.enabled !== false} />
              </div>
              {processor.description && (
                <p className="mt-2 text-xs text-slate-400">{processor.description}</p>
              )}
              <div className="mt-2 flex gap-1.5 flex-wrap">
                {(processor.tool_groups ?? []).map(tool => (
                  <span key={tool} className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px]">
                    {tool}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-slate-950/70 border border-slate-800 rounded p-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-slate-100">{value}</div>
    </div>
  )
}

function SchemaOverview({ schema }: { schema: Record<string, SchemaSection> }) {
  const entries = Object.entries(schema)

  if (entries.length === 0) {
    return (
      <div className="border border-slate-800 bg-slate-950/60 rounded p-3 text-xs text-slate-500">
        No extraction schema sections defined.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {entries.map(([sectionKey, section]) => {
        const fields = Object.entries(section.item_schema ?? {})
        return (
          <div key={sectionKey} className="border border-slate-700 bg-slate-900/40 rounded overflow-hidden">
            <div className="px-3 py-2 border-b border-slate-800 bg-slate-950/40">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-sm text-slate-100">{sectionKey}</span>
                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px]">
                  {section.type ?? 'list'}
                </span>
                <span className="text-[11px] text-slate-500">
                  {fields.length} field{fields.length === 1 ? '' : 's'}
                </span>
              </div>
              {section.description && (
                <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                  {section.description}
                </p>
              )}
            </div>
            {fields.length === 0 ? (
              <div className="px-3 py-3 text-xs text-slate-500">No fields defined.</div>
            ) : (
              <div className="divide-y divide-slate-800">
                {fields.map(([fieldKey, field]) => (
                  <div
                    key={fieldKey}
                    className="grid grid-cols-[minmax(120px,1.2fr)_90px_80px_minmax(180px,2fr)] gap-3 px-3 py-2 items-start"
                  >
                    <span className="font-mono text-xs text-slate-200 break-words">{fieldKey}</span>
                    <span className="text-xs text-slate-300">{field.type ?? 'string'}</span>
                    <span className={field.required ? 'text-xs text-teal-300' : 'text-xs text-slate-500'}>
                      {field.required ? 'required' : 'optional'}
                    </span>
                    <span className="text-xs text-slate-400 leading-relaxed">
                      {field.description || '—'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function FieldDescriptionsView({
  descriptions,
}: {
  descriptions: Record<string, unknown>
}) {
  return (
    <div className="space-y-2">
      {Object.entries(descriptions).map(([key, value]) => (
        <div key={key} className="border border-slate-800 bg-slate-950/50 rounded p-3">
          <div className="font-mono text-xs text-slate-200">{key}</div>
          <p className="mt-1 text-xs text-slate-400 whitespace-pre-wrap leading-relaxed">
            {stringifyFieldDescription(value) || '—'}
          </p>
        </div>
      ))}
    </div>
  )
}

function PromptBlock({ text }: { text: string }) {
  return (
    <div className="bg-slate-950/70 border border-slate-800 text-slate-300 text-xs p-3 rounded max-h-[34vh] overflow-auto whitespace-pre-wrap leading-relaxed">
      {text}
    </div>
  )
}

function StatusPill({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${
        enabled ? 'bg-teal-900/70 text-teal-200' : 'bg-slate-800 text-slate-400'
      }`}
    >
      {enabled ? 'On' : 'Off'}
    </span>
  )
}

function SettingPanel({
  label,
  enabled,
  enabledText,
  disabledText,
}: {
  label: string
  enabled: boolean
  enabledText: string
  disabledText: string
}) {
  return (
    <div className="border border-slate-700 bg-slate-900/40 rounded p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-300">{label}</span>
        <StatusPill enabled={enabled} />
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {enabled ? enabledText : disabledText}
      </p>
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

function ReviewPromptView({
  custom,
  purpose,
  onUseAsCustom,
}: {
  custom: string | null
  purpose: string
  onUseAsCustom: (defaultPrompt: string) => void
}) {
  const [defaultPrompt, setDefaultPrompt] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (custom) {
      setDefaultPrompt(null)
      return
    }
    let cancelled = false
    setErr(null)
    getDefaultReviewPrompt(purpose)
      .then(res => { if (!cancelled) setDefaultPrompt(res.review_prompt) })
      .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [custom, purpose])

  if (custom) {
    return (
      <pre className="bg-slate-950 text-slate-200 text-xs font-mono p-3 rounded max-h-[30vh] overflow-auto whitespace-pre-wrap">
{String(custom)}
      </pre>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-400 italic">
          Using built-in default for <code>{purpose}</code>.
        </span>
        <button
          type="button"
          onClick={() => defaultPrompt && onUseAsCustom(defaultPrompt)}
          disabled={!defaultPrompt}
          className="px-2 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 rounded text-xs"
          title="Open the editor with this default pre-filled as review_prompt"
        >
          Customize from this default
        </button>
      </div>
      {err ? (
        <p className="text-xs text-rose-300">{err}</p>
      ) : defaultPrompt == null ? (
        <p className="text-xs text-slate-500">Loading default prompt…</p>
      ) : (
        <pre className="bg-slate-950 text-slate-300 text-xs font-mono p-3 rounded max-h-[30vh] overflow-auto whitespace-pre-wrap border border-slate-800">
{defaultPrompt}
        </pre>
      )}
    </div>
  )
}
