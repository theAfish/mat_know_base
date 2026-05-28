import client from './client'
import type { Space, SpaceCreatePayload, SpaceUpdatePayload } from '../types'

export const listSpaces = () =>
  client.get<Space[]>('/spaces').then(r => r.data)

export const getSpace = (idOrName: string) =>
  client.get<Space>(`/spaces/${idOrName}`).then(r => r.data)

export const createSpace = (payload: SpaceCreatePayload) =>
  client.post<{ space_id: string; name: string; purpose?: string }>('/spaces', payload).then(r => r.data)

export const updateSpace = (spaceId: string, changes: SpaceUpdatePayload) =>
  client.put<{ space_id: string; version: number; purpose?: string }>(`/spaces/${spaceId}`, changes).then(r => r.data)

export const deleteSpace = (spaceId: string) =>
  client.delete<{ ok: boolean; deleted?: string }>(`/spaces/${spaceId}`).then(r => r.data)

export const getDefaultReviewPrompt = (purpose: string) =>
  client
    .get<{ purpose: string; review_prompt: string }>('/spaces/_defaults/review-prompt', {
      params: { purpose },
    })
    .then(r => r.data)
