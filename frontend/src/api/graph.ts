import client from './client'
import type { GraphPayload } from '../types'

export const getKnowledgeGraph = (params?: { project_id?: string }) =>
  client.get<GraphPayload>('/graph', { params }).then(r => r.data)

export const getReviewCounts = () =>
  client.get<Record<string, unknown>>('/graph/review-counts').then(r => r.data)

export const reviewGraph = (params: { mode?: string; seed_count?: number }) =>
  client.post<{ job_id: string }>('/graph/review', params).then(r => r.data)

export const clearGraph = () =>
  client.post('/graph/clear').then(r => r.data)
