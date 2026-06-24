import type { Project } from '../types'


export function projectDisplayName(project: Project | null | undefined): string {
  if (!project) return ''

  const label = project.label?.trim()
  if (label) return label

  const sourcePath = project.source_path?.trim() ?? ''
  if (sourcePath) {
    const normalized = sourcePath.replace(/[/\\]+$/, '')
    if (normalized) {
      const parts = normalized.split(/[/\\]/)
      const folder = parts[parts.length - 1]
      if (folder) return folder
    }
  }

  return project.project_id.slice(0, 12)
}
