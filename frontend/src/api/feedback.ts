import client from './client'
import type { FeedbackItem } from '../types'

export const listFeedback = (params?: {
  limit?: number
  status?: string
  project_id?: string
}) => client.get<FeedbackItem[]>('/feedback', { params }).then(r => r.data)

export const getFeedbackSummary = (projectId: string) =>
  client.get(`/feedback/summary/${projectId}`).then(r => r.data)

export const resolveFeedback = (id: string, status: string, notes = '') =>
  client.post(`/feedback/${id}/resolve`, { status, notes }).then(r => r.data)

export const reviewFeedback = (params?: { project_id?: string }) =>
  client.post<{ job_id: string }>('/feedback/review', params ?? {}).then(r => r.data)
