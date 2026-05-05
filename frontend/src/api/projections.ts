import client from './client'
import type { Projection } from '../types'

export const listProjections = (params?: {
  limit?: number
  space_id?: string
  project_id?: string
  include_data?: boolean
  newest_only?: boolean
}) => client.get<Projection[]>('/projections', { params }).then(r => r.data)

export const getProjection = (id: string) =>
  client.get<Projection>(`/projections/${id}`).then(r => r.data)

export const reviewProjections = (params: { space_id?: string; project_id?: string }) =>
  client.post<{ job_id: string }>('/projections/review', params).then(r => r.data)
