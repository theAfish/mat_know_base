import client from './client'
import type { PostProcessorScript } from '../types'

export const listPostProcessorScripts = () =>
  client.get<PostProcessorScript[]>('/post-processor-scripts').then(r => r.data)

export const uploadPostProcessorScript = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return client.post<PostProcessorScript>('/post-processor-scripts/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000,
  }).then(r => r.data)
}