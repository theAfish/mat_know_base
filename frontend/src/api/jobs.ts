import client from './client'
import type { Job } from '../types'

export const listJobs = (params?: { limit?: number }) =>
  client.get<Job[]>('/jobs', { params }).then(r => r.data)

export const getJob = (id: string) =>
  client.get<Job>(`/jobs/${id}`).then(r => r.data)

export const cancelJob = (id: string) =>
  client.post<{ ok: boolean }>(`/jobs/${id}/cancel`).then(r => r.data)

export const cancelAllJobs = (params?: { project_id?: string }) =>
  client.post<{ ok: boolean; cancelled: string[]; count: number }>('/jobs/cancel-all', null, { params }).then(r => r.data)
