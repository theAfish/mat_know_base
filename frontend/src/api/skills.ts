import client from './client'
import type { CustomSkill } from '../types'

export const listSkills = () =>
  client.get<CustomSkill[]>('/skills').then(r => r.data)

export const getSkill = (idOrSlug: string) =>
  client.get<CustomSkill>(`/skills/${idOrSlug}`).then(r => r.data)

export const uploadSkill = (files: File[]) => {
  const form = new FormData()
  files.forEach(file => {
    const relPath = (file as File & { webkitRelativePath?: string }).webkitRelativePath
    form.append('files', file, relPath || file.name)
  })
  return client
    .post<CustomSkill>('/skills/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120_000,
    })
    .then(r => r.data)
}

export const deleteSkill = (skillId: string) =>
  client.delete<{ ok: boolean; deleted?: string }>(`/skills/${skillId}`).then(r => r.data)
