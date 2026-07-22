import type { PostProcessorProfile, Space, SpaceCreatePayload } from '../../types'

export const PURPOSE_OPTIONS = ['tabular_database', 'qa_benchmark', 'skill_cards', 'freeform'] as const
export const REVIEW_SEARCH_TOOL_OPTIONS = ['web', 'uniprot', 'ncbi', 'crossref'] as const
export const POST_PROCESSOR_TOOL_OPTIONS = ['reading', 'web', 'uniprot', 'ncbi', 'crossref'] as const
export const PURPOSE_COLORS: Record<string, string> = {
  tabular_database: 'bg-teal-700 text-teal-100', qa_benchmark: 'bg-indigo-700 text-indigo-100',
  skill_cards: 'bg-amber-700 text-amber-100', freeform: 'bg-slate-600 text-slate-100',
}
export const FIELD_TYPE_OPTIONS = ['string', 'number', 'integer', 'boolean', 'list', 'object'] as const

export type SchemaField = { type?: string; item_type?: string; required?: boolean; description?: string; [key: string]: unknown }
export type SchemaSection = { type?: string; description?: string; filter?: Record<string, unknown>; item_schema?: Record<string, SchemaField>; [key: string]: unknown }
export type SpaceDraft = {
  name: string; domain: string; purpose: string; description: string
  extraction_schema: Record<string, SchemaSection>; system_prompt: string; review_prompt: string
  review_trackable: boolean; review_allow_search: boolean; review_search_tools: string[]
  post_processors: PostProcessorProfile[]
}
export interface EditorState { mode: 'create' | 'edit' | 'import'; space?: Space; draft: SpaceDraft }

const DEFAULT_PROCESSOR: PostProcessorProfile = {
  id: 'default', name: 'Default reviewer', description: 'General projection review and correction.',
  prompt: null, tool_groups: ['reading'], skill_ids: [], script: null, output_columns: [], enabled: true,
}
export const EMPTY_DRAFT: SpaceDraft = {
  name: 'my_new_space', domain: '', purpose: 'tabular_database', description: '', extraction_schema: {},
  system_prompt: '', review_prompt: '', review_trackable: true, review_allow_search: false,
  review_search_tools: ['web'], post_processors: [DEFAULT_PROCESSOR],
}
export const toRecord = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const description = (value: unknown) => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2)
const mergedDescription = (current: unknown, legacy: unknown) => {
  const existing = typeof current === 'string' ? current.trim() : ''
  const guidance = description(legacy).trim()
  return !guidance || existing.includes(guidance) ? existing : existing ? `${existing}\n\nExtraction guidance: ${guidance}` : guidance
}
const normalizeField = (value: unknown): SchemaField => {
  const field = toRecord(value)
  return { ...field, type: typeof field.type === 'string' ? field.type : 'string', item_type: typeof field.item_type === 'string' ? field.item_type : 'string', required: typeof field.required === 'boolean' ? field.required : false, description: typeof field.description === 'string' ? field.description : '' }
}
export const normalizeSchema = (value: unknown, legacyValue?: unknown): Record<string, SchemaSection> => {
  const legacy = toRecord(legacyValue)
  return Object.fromEntries(Object.entries(toRecord(value)).map(([key, raw]) => {
    const section = toRecord(raw)
    return [key, { ...section, type: typeof section.type === 'string' ? section.type : 'list', description: mergedDescription(section.description, legacy[key]), filter: toRecord(section.filter), item_schema: Object.fromEntries(Object.entries(toRecord(section.item_schema)).map(([fieldKey, field]) => [fieldKey, normalizeField(field)])) }]
  }))
}
export const slugifyProcessorId = (value: string) => value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'reviewer'
export const parseOutputColumns = (value: string) => Array.from(new Set(value.split(',').map(item => item.trim()).filter(Boolean))).map(name => ({ name }))
export const normalizeOutputColumns = (value: unknown): Array<{ name: string; description?: string }> => {
  if (typeof value === 'string') return parseOutputColumns(value)
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  return value.flatMap(item => {
    const object = toRecord(item)
    const name = String(object.name ?? object.id ?? (typeof item === 'object' ? '' : item) ?? '').trim()
    if (!name || seen.has(name.toLowerCase())) return []
    seen.add(name.toLowerCase())
    return [typeof object.description === 'string' ? { name, description: object.description } : { name }]
  })
}
export const outputColumnNames = (columns: PostProcessorProfile['output_columns']) => (columns ?? []).map(column => column.name).filter(Boolean).join(', ')
export const normalizePostProcessors = (value: unknown): PostProcessorProfile[] => {
  if (!Array.isArray(value) || !value.length) return [DEFAULT_PROCESSOR]
  return value.filter(item => item && typeof item === 'object' && !Array.isArray(item)).map((item, index) => {
    const object = item as Record<string, unknown>
    const name = typeof object.name === 'string' && object.name.trim() ? object.name : `Reviewer ${index + 1}`
    const tools = (Array.isArray(object.tool_groups) ? object.tool_groups : []).map(String).filter(tool => POST_PROCESSOR_TOOL_OPTIONS.includes(tool as typeof POST_PROCESSOR_TOOL_OPTIONS[number]))
    const script = toRecord(object.script); const scriptId = String(script.script_id ?? '').trim(); const timeout = Number(script.timeout_seconds)
    return { id: slugifyProcessorId(typeof object.id === 'string' && object.id.trim() ? object.id : name), name, description: typeof object.description === 'string' ? object.description : '', prompt: typeof object.prompt === 'string' && object.prompt.trim() ? object.prompt : null, tool_groups: tools.length ? Array.from(new Set(tools)) : ['reading'], skill_ids: Array.isArray(object.skill_ids) ? Array.from(new Set(object.skill_ids.map(String).filter(Boolean))) : [], script: scriptId ? { script_id: scriptId, timeout_seconds: Number.isInteger(timeout) && timeout >= 1 && timeout <= 300 ? timeout : 30 } : null, output_columns: normalizeOutputColumns(object.output_columns ?? object.allowed_output_columns ?? object.new_columns), enabled: typeof object.enabled === 'boolean' ? object.enabled : true }
  })
}
export const draftFromObject = (object: Record<string, unknown>): SpaceDraft => ({ name: typeof object.name === 'string' ? object.name : EMPTY_DRAFT.name, domain: typeof object.domain === 'string' ? object.domain : '', purpose: typeof object.purpose === 'string' ? object.purpose : 'tabular_database', description: typeof object.description === 'string' ? object.description : '', extraction_schema: normalizeSchema(object.extraction_schema, object.field_descriptions), system_prompt: typeof object.system_prompt === 'string' ? object.system_prompt : '', review_prompt: typeof object.review_prompt === 'string' ? object.review_prompt : '', review_trackable: typeof object.review_trackable === 'boolean' ? object.review_trackable : true, review_allow_search: typeof object.review_allow_search === 'boolean' ? object.review_allow_search : false, review_search_tools: Array.isArray(object.review_search_tools) ? object.review_search_tools.map(String) : ['web'], post_processors: normalizePostProcessors(object.post_processors) })
export const draftFromSpace = (space: Space, reviewPrompt?: string) => draftFromObject({ ...space, field_descriptions: space.field_descriptions ?? {}, review_prompt: reviewPrompt ?? space.review_prompt ?? '' })
export const draftToPayload = (draft: SpaceDraft): SpaceCreatePayload => ({ name: draft.name.trim(), domain: draft.domain.trim(), purpose: draft.purpose, description: draft.description, extraction_schema: draft.extraction_schema, system_prompt: draft.system_prompt, field_descriptions: {}, review_prompt: draft.review_prompt.trim() ? draft.review_prompt : null, review_trackable: draft.review_trackable, review_allow_search: draft.review_allow_search, review_search_tools: draft.review_search_tools.length ? draft.review_search_tools : ['web'], post_processors: draft.post_processors })
