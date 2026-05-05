import client from './client'
import type { UploadProject } from '../types'

export const uploadInit = (): Promise<{ upload_id: string }> =>
  client.post('/upload/init').then(r => r.data)

export const uploadFile = (
  uploadId: string,
  file: File,
  relativePath: string,
  uploadPath: string,
  onProgress?: (pct: number) => void,
) => {
  const form = new FormData()
  form.append('upload_id', uploadId)
  form.append('relative_path', relativePath)
  form.append('upload_path', uploadPath)
  form.append('file', file, file.name)
  return client.post('/upload/file', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: e => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  }).then(r => r.data)
}

export const uploadComplete = (uploadId: string) =>
  client.post('/upload/complete', { upload_id: uploadId }).then(r => r.data)

export const uploadIngest = (payload: UploadProject[]): Promise<{ job_id: string }> =>
  client.post('/upload/ingest', payload).then(r => r.data)
