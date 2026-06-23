import client from './client'
import type {
  SchemaProposal,
  SchemaProposalReviewResult,
  SchemaProposalRevision,
  WorkflowSchemaStatus,
  WorkflowMaintenanceTask,
} from '../types'

export const getWorkflowSchemaStatus = () =>
  client.get<WorkflowSchemaStatus>('/workflow-schema').then(r => r.data)

export const curateWorkflowSchema = (minSupport: number, author: string) =>
  client.post<{ job_id: string }>('/workflow-schema/curate', {
    min_support: minSupport,
    author,
  }).then(r => r.data)

export const listSchemaProposals = (status?: string) =>
  client.get<SchemaProposal[]>('/workflow-schema/proposals', {
    params: { status: status ?? '' },
  }).then(r => r.data)

export const reviewSchemaProposal = (
  proposalId: string,
  decision: 'approve' | 'reject' | 'request_revision',
  reviewer: string,
  notes: string,
) =>
  client.post<SchemaProposalReviewResult>(
    `/workflow-schema/proposals/${proposalId}/review`,
    { decision, reviewer, notes },
  ).then(r => r.data)

export const editSchemaProposal = (
  proposalId: string,
  draft: {
    payload: Record<string, unknown>
    evidence_workflow_ids: string[]
    rationale: string
    editor: string
    change_note: string
  },
) => client.patch<{
  proposal_id: string
  status: string
  revision_number: number
  validation_errors: string[]
}>(`/workflow-schema/proposals/${proposalId}`, draft).then(r => r.data)

export const getSchemaProposalRevisions = (proposalId: string) =>
  client.get<SchemaProposalRevision[]>(
    `/workflow-schema/proposals/${proposalId}/revisions`,
  ).then(r => r.data)

export const listPendingRecanonicalizations = () =>
  client.get<WorkflowMaintenanceTask[]>('/workflow-maintenance', {
    params: { status: 'pending' },
  }).then(r => r.data.filter(task => task.task_type === 'recanonicalize'))

export const runWorkflowMaintenanceTask = (taskId: string) =>
  client.post<{ job_id: string; task_id: string }>(
    `/workflow-maintenance/${taskId}/run`,
  ).then(r => r.data)

export const runPendingRecanonicalizations = () =>
  client.post<{ job_id: string }>(
    '/workflow-maintenance-batch/recanonicalize',
  ).then(r => r.data)
