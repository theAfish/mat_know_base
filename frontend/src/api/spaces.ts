import client from './client'
import type { Space } from '../types'

export const listSpaces = () =>
  client.get<Space[]>('/spaces').then(r => r.data)

export const getSpace = (idOrName: string) =>
  client.get<Space>(`/spaces/${idOrName}`).then(r => r.data)
