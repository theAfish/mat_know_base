import client from './client'
import type { Job } from '../types'

export const listJobs = (params?: { limit?: number }) =>
  client.get<Job[]>('/jobs', { params }).then(r => r.data)

export const getJob = (id: string) =>
  client.get<Job>(`/jobs/${id}`).then(r => r.data)
