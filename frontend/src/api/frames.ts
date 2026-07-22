import client from './client'
import type { ExtractionPass, Frame } from '../types'

export const listFrames = () =>
  client.get<Frame[]>('/frames').then(r => r.data)

export const getFrame = (projectId: string) =>
  client.get<Frame>(`/frames/${projectId}`).then(r => r.data)

export const getFrameHistory = (projectId: string) =>
  client.get<ExtractionPass[]>(`/frames/${projectId}/history`).then(r => r.data)
