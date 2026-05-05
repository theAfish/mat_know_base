import client from './client'
import type { Project, Asset, ProcessedAsset, Job } from '../types'

export const listProjects = (limit = 100) =>
  client.get<Project[]>('/projects', { params: { limit } }).then(r => r.data)

export const getProject = (id: string) =>
  client.get<Project>(`/projects/${id}`).then(r => r.data)

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

export const projectToSpace = (id: string, spaceId: string) =>
  client.post<{ job_id: string }>(
    `/projects/${id}/project`,
    { space_id: spaceId },
  ).then(r => r.data)

export const kgExtractProject = (id: string) =>
  client.post<{ job_id: string }>(`/projects/${id}/kg-extract`).then(r => r.data)

export const getProjectJobs = (id: string) =>
  client.get<Job[]>(`/projects/${id}/jobs`).then(r => r.data)

export const searchLibrary = (q: string, limit = 20) =>
  client.get('/search', { params: { q, limit } }).then(r => r.data)
