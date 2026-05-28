import client from './client'
import type { ProjectGroup, ProjectGroupCreatePayload, ProjectGroupUpdatePayload } from '../types'

export const listProjectGroups = () =>
  client.get<ProjectGroup[]>('/project-groups').then(r => r.data)

export const createProjectGroup = (payload: ProjectGroupCreatePayload) =>
  client.post<ProjectGroup>('/project-groups', payload).then(r => r.data)

export const updateProjectGroup = (id: string, payload: ProjectGroupUpdatePayload) =>
  client.patch<ProjectGroup>(`/project-groups/${id}`, payload).then(r => r.data)

export const deleteProjectGroup = (id: string) =>
  client.delete<{ group_id: string; deleted: boolean; unassigned_projects: number }>(
    `/project-groups/${id}`,
  ).then(r => r.data)

export const assignProjectsToGroup = (projectIds: string[], groupId: string | null) =>
  client.post<{ updated: number; group_id: string | null }>(
    '/project-groups/assign',
    { project_ids: projectIds, group_id: groupId },
  ).then(r => r.data)
