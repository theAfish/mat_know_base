// ─── Domain entities ─────────────────────────────────────────────────────────

export interface Project {
  project_id: string
  label: string | null
  source_path: string | null
  file_count: number
  asset_count: number
  processing_status: string
  frame_status: string | null
  workflow_status: string
  workflow_version: number | null
  canonical_workflow_status: string
  canonical_workflow_version: number | null
  created_at: string
  group_id?: string | null
}

export interface ProjectGroup {
  group_id: string
  name: string
  description: string | null
  color: string | null
  display_order: number
  project_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ProjectGroupCreatePayload {
  name: string
  description?: string | null
  color?: string | null
  display_order?: number | null
}

export type ProjectGroupUpdatePayload = Partial<ProjectGroupCreatePayload>

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
  primary_relpath?: string | null
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
  source_type?: string
  times_reviewed: number
  review_notes: string | null
  reviewed_at: string | null
  data: Record<string, unknown>
  /** Non-null when this projection has been superseded by a newer review. */
  superseded_by_id: string | null
  /** List of projection IDs this row consolidated (set on the NEW reviewed row). */
  supersedes_ids: string[] | null
}

export interface Space {
  space_id: string
  name: string
  description: string
  domain: string
  purpose?: string
  extraction_schema: Record<string, unknown>
  system_prompt?: string
  field_descriptions?: Record<string, unknown>
  review_prompt?: string | null
  review_trackable?: boolean
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface SpaceCreatePayload {
  name: string
  domain: string
  extraction_schema: Record<string, unknown>
  system_prompt: string
  field_descriptions: Record<string, unknown>
  description?: string
  purpose?: string
  review_prompt?: string | null
  review_trackable?: boolean
}

export type SpaceUpdatePayload = Partial<SpaceCreatePayload> & { name?: string }

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
  status: 'QUEUED' | 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
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

export interface RawWorkflowNode {
  node_id: string
  raw_name: string
  node_kind_guess: 'object' | 'operation' | 'unknown'
  attributes_explicitly_mentioned: Record<string, unknown>
  evidence_text: string
  paper_location: Record<string, unknown>
  confidence: number
}

export interface RawWorkflowEdge {
  edge_id: string
  source_node: string
  target_node: string
  relation_type: string
  evidence_text: string
  paper_location?: Record<string, unknown> | null
  confidence: number
}

export interface RawWorkflowGraph {
  schema_version: string
  paper_id: string
  extraction_id: string
  nodes: RawWorkflowNode[]
  edges: RawWorkflowEdge[]
}

export interface RawWorkflowVersion {
  extraction_id: string
  project_id: string
  version: number
  schema_version: string
  extractor_version: string
  status: string
  record_status: string
  supersedes_extraction_id: string | null
  model: string | null
  provenance: Record<string, unknown>
  error: string | null
  created_at: string | null
  extracted_at: string | null
  node_count?: number
  edge_count?: number
  graph?: RawWorkflowGraph | null
}

export interface CanonicalWorkflowNode {
  node_id: string
  label: string
  node_kind: 'object' | 'operation'
  object_schema?: string | null
  operation_template_id?: string | null
  attributes: Record<string, unknown>
  raw_node_ids: string[]
}

export interface CanonicalWorkflowGraph {
  schema_version: string
  canonicalization_id: string
  paper_id: string
  raw_extraction_id: string
  nodes: CanonicalWorkflowNode[]
  edges: Array<{ edge_id: string; source_node: string; target_node: string; relation_type: string; raw_edge_ids: string[] }>
  raw_to_canonical_mappings: Array<Record<string, unknown>>
  unmatched_raw_information: Array<Record<string, unknown>>
  granularity_mappings: Array<Record<string, unknown>>
  proposed_schema_updates: Array<Record<string, unknown>>
}

export interface CanonicalWorkflowVersion {
  canonicalization_id: string
  project_id: string
  raw_extraction_id: string
  version: number
  schema_version: string
  canonicalizer_version: string
  status: string
  model: string | null
  provenance: Record<string, unknown>
  error: string | null
  created_at: string | null
  canonicalized_at: string | null
  node_count?: number
  edge_count?: number
  graph?: CanonicalWorkflowGraph | null
}

// ─── Upload types ─────────────────────────────────────────────────────────────

export interface UploadFileEntry {
  name: string
  relativePath: string
  uploadPath: string
}

export interface UploadProject {
  name: string
  upload_id: string
  files: UploadFileEntry[]
  /** False when the user manually edited the name in the upload preview.
   *  Defaults to true (auto-generated) so the backend can later auto-rename
   *  the project from the extracted paper title. */
  name_auto?: boolean
}

export interface UploadExpandFile {
  uploadPath: string
  size: number
}

export interface UploadExpandResponse {
  files: UploadExpandFile[]
  extracted: Array<{ archive: string; count: number }>
  failed: Array<{ archive: string; error: string }>
}

// ─── UI ───────────────────────────────────────────────────────────────────────

export type Page = 'assistant' | 'projects' | 'frames' | 'graph' | 'projections' | 'feedback' | 'spaces' | 'settings'
