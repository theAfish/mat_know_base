import client from './client'
import type { Project, Asset, ProcessedAsset, Job, RawWorkflowVersion, CanonicalWorkflowVersion } from '../types'

export const listProjects = (limit = 100) =>
  client.get<Project[]>('/projects', { params: { limit } }).then(r => r.data)

export const getProject = (id: string) =>
  client.get<Project>(`/projects/${id}`).then(r => r.data)

export const renameProject = (id: string, label: string) =>
  client.patch<{ project_id: string; label: string; user_named: boolean }>(
    `/projects/${id}`,
    { label },
  ).then(r => r.data)

export const listAssets = (id: string) =>
  client.get<Asset[]>(`/projects/${id}/assets`).then(r => r.data)

export const listProcessedAssets = (id: string) =>
  client.get<ProcessedAsset[]>(`/projects/${id}/processed-assets`).then(r => r.data)

export const processProject = (id: string) =>
  client.post<{ job_id: string }>(`/projects/${id}/process`).then(r => r.data)

export const extractProject = (id: string, spaceId?: string) =>
  client.post<{ job_id: string }>(
    `/projects/${id}/extract`,
    spaceId ? { space_id: spaceId } : {},
  ).then(r => r.data)

export const projectToSpace = (
  id: string,
  spaceId: string,
  sourceType: 'frame' | 'markdown' = 'frame',
) =>
  client.post<{ job_id: string }>(
    `/projects/${id}/project`,
    { space_id: spaceId, source_type: sourceType },
  ).then(r => r.data)

export const kgExtractProject = (id: string) =>
  client.post<{ job_id: string }>(`/projects/${id}/kg-extract`).then(r => r.data)

export const workflowExtractProject = (id: string) =>
  client.post<{ job_id: string }>(`/projects/${id}/workflow-extract`).then(r => r.data)

export const listProjectWorkflows = (id: string) =>
  client.get<RawWorkflowVersion[]>(`/projects/${id}/workflows`).then(r => r.data)

export const getProjectWorkflow = (id: string, version?: number) =>
  client.get<RawWorkflowVersion>(
    `/projects/${id}/workflows/${version == null ? 'latest' : version}`,
  ).then(r => r.data)

export const canonicalizeProjectWorkflow = (id: string, rawExtractionId?: string) =>
  client.post<{ job_id: string }>(`/projects/${id}/workflows/canonicalize`, {
    raw_extraction_id: rawExtractionId ?? null,
  }).then(r => r.data)

export const listCanonicalWorkflows = (id: string) =>
  client.get<CanonicalWorkflowVersion[]>(`/projects/${id}/canonical-workflows`).then(r => r.data)

export const getCanonicalWorkflow = (id: string, version?: number) =>
  client.get<CanonicalWorkflowVersion>(
    `/projects/${id}/canonical-workflows/${version == null ? 'latest' : version}`,
  ).then(r => r.data)

export const getProjectJobs = (id: string) =>
  client.get<Job[]>(`/projects/${id}/jobs`).then(r => r.data)

export const deleteProject = (id: string, deleteS3 = true) =>
  client.delete<Record<string, unknown>>(`/projects/${id}`, { params: { delete_s3: deleteS3 } }).then(r => r.data)

export const searchLibrary = (q: string, limit = 20) =>
  client.get('/search', { params: { q, limit } }).then(r => r.data)
