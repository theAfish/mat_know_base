// ─── Domain entities ─────────────────────────────────────────────────────────

export interface Project {
  project_id: string
  label: string | null
  source_path: string | null
  file_count: number
  asset_count: number
  frame_status: string | null
  created_at: string
}

export interface Asset {
  asset_id: string
  filename: string
  mime_type: string | null
  size_bytes: number
  sha256?: string
  status?: string
  created_at?: string
}

export interface ProcessedAsset {
  processed_asset_id: string
  asset_id: string
  filename: string | null
  processing_type: string
  output_format: string
  artifact_count: number
  created_at: string
}

export interface Frame {
  project_id: string
  frame_id: string | null
  status: string
  extraction_version: number
  extracted_at: string | null
  extraction_summary: string | null
  times_checked: number
  created_at: string | null
  updated_at: string | null
  // list endpoint returns summary fields only; detail endpoint adds:
  content: Record<string, unknown> | null
  source_metadata: Record<string, unknown> | null
  agent_annotations: {
    clarifications?: Array<Record<string, unknown>>
    resolved_feedback?: Array<Record<string, unknown>>
  } | null
}

export interface ExtractionPass {
  pass_id: string
  pass_number: number
  pass_type: string
  changes_made: boolean
  agent_notes: string | null
  created_at: string
}

export interface Projection {
  projection_id: string
  space_id: string
  frame_id: string
  project_id: string
  status: string
  agent_notes: string | null
  extracted_at: string | null
  created_at: string
  space_version: number
  times_reviewed: number
  review_notes: string | null
  reviewed_at: string | null
  data: Record<string, unknown>
}

export interface Space {
  space_id: string
  name: string
  description: string
  domain: string
  extraction_schema: Record<string, unknown>
}

export interface FeedbackItem {
  feedback_id: string
  source_agent: string
  source_projection_id: string | null
  target_frame_id: string | null
  target_project_id: string | null
  category: string
  field_path: string | null
  question: string
  context: string | null
  status: string
  resolution_notes: string | null
  resolved_by: string | null
  resolved_at: string | null
  created_at: string
}

export interface Job {
  job_id: string
  kind: string
  label: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  project_id: string | null
  result: Record<string, unknown> | null
  error: string | null
  current_message: string | null
  events: Array<{ message: string; stage?: string }>
  created_at: string
  updated_at: string
}

export interface GraphConcept {
  // label is used as the unique identifier
  label: string
  aliases?: string[]
  source_project_ids?: string[]
  source_frame_ids?: string[]
  knowledge_refs?: Array<Record<string, unknown>>
  // computed / optional enrichment:
  review_count?: number
  modification_count?: number
  degree?: number
}

export interface GraphRelation {
  source: string   // concept label
  target: string   // concept label
  relation: string // relation type name
  evidence_level?: number  // 1-4
  source_project_id?: string
  source_frame_id?: string
  knowledge_ref?: Record<string, unknown>
  review_count?: number
  modification_count?: number
}

export interface KnowledgeGraph {
  concepts: GraphConcept[]
  relations: GraphRelation[]
}

export interface GraphPayload {
  graph: KnowledgeGraph
  projection_count: number
}

// ─── Upload types ─────────────────────────────────────────────────────────────

export interface UploadFileEntry {
  name: string
  relativePath: string
  uploadPath: string
}

export interface UploadProject {
  name: string
  files: UploadFileEntry[]
}

// ─── UI ───────────────────────────────────────────────────────────────────────

export type Page = 'assistant' | 'projects' | 'frames' | 'graph' | 'projections' | 'feedback'
