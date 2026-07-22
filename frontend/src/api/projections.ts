import client from './client'
import type { Projection } from '../types'

export const listProjections = (params?: {
  limit?: number
  space_id?: string
  project_id?: string
  include_data?: boolean
  newest_only?: boolean
  include_history?: boolean
}) => client.get<Projection[]>('/projections', { params }).then(r => r.data)

export const getProjection = (id: string) =>
  client.get<Projection>(`/projections/${id}`).then(r => r.data)

export type ReviewMode = 'per_project' | 'session'

export const reviewProjections = (params: {
  space_id?: string
  project_id?: string
  project_ids?: string[]
  mode?: ReviewMode
  reviewer_id?: string
}) =>
  client.post<{ job_id: string; job_ids?: string[] }>('/projections/review', params).then(r => r.data)

export const deleteProjection = (id: string) =>
  client.delete(`/projections/${id}`)

export const exportProjections = async (
  projectionIds: string[],
  format: 'yaml' | 'json',
): Promise<void> => {
  const resp = await client.post(
    '/projections/export',
    { projection_ids: projectionIds, format },
    { responseType: 'blob' },
  )
  const contentDisposition: string = resp.headers['content-disposition'] ?? ''
  const match = contentDisposition.match(/filename="?([^";\n]+)"?/)
  const filename = match ? match[1] : `selected_projections.${format === 'json' ? 'json' : 'zip'}`
  const url = URL.createObjectURL(resp.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
