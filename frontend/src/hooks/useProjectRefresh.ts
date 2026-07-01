import { useCallback } from 'react'

import { getProject } from '../api/projects'
import type { Project } from '../types'

export function useProjectRefresh(
  projectId: string,
  onProjectUpdated?: (project: Project) => void,
) {
  return useCallback(() => {
    if (!onProjectUpdated) return
    getProject(projectId).then(onProjectUpdated).catch(() => { /* ignore refresh failures */ })
  }, [projectId, onProjectUpdated])
}

