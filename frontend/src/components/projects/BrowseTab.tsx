import { useCallback, useEffect, useState } from 'react'

import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import { getProject, listProjects } from '../../api/projects'
import ProjectGroupedList from '../ProjectGroupedList'
import type { Job, Project, Space } from '../../types'
import ProjectDetail from './ProjectDetail'
import StatusLights from './StatusLights'

function projectsEqual(a: Project, b: Project): boolean {
  return (
    a.project_id === b.project_id &&
    a.label === b.label &&
    a.source_path === b.source_path &&
    a.file_count === b.file_count &&
    a.asset_count === b.asset_count &&
    a.processing_status === b.processing_status &&
    a.frame_status === b.frame_status &&
    a.workflow_status === b.workflow_status &&
    a.workflow_version === b.workflow_version &&
    a.created_at === b.created_at &&
    (a.group_id ?? null) === (b.group_id ?? null)
  )
}

function mergeProjectList(current: Project[], next: Project[]): Project[] {
  const currentById = new Map(current.map(p => [p.project_id, p]))
  let changed = current.length !== next.length

  const merged = next.map(project => {
    const existing = currentById.get(project.project_id)
    if (existing && projectsEqual(existing, project)) return existing
    changed = true
    return project
  })

  return changed ? merged : current
}

function mergeProject(current: Project, next: Project): Project {
  return projectsEqual(current, next) ? current : next
}

export default function BrowseTab({ spaces }: { spaces: Space[] }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Project | null>(null)

  const load = useCallback(async () => {
    try {
      // Don't blank the table during a background refresh — only show the
      // loading placeholder on the very first fetch.
      const data = await listProjects(5000)
      setProjects(prev => mergeProjectList(prev, data))
      setSelected(prev => {
        if (!prev) return prev
        const updated = data.find(p => p.project_id === prev.project_id)
        return updated ? mergeProject(prev, updated) : null
      })
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshProject = useCallback(async (projectId: string) => {
    const updated = await getProject(projectId)
    setProjects(prev => prev.map(p =>
      p.project_id === updated.project_id ? mergeProject(p, updated) : p
    ))
    setSelected(prev =>
      prev?.project_id === updated.project_id ? mergeProject(prev, updated) : prev
    )
  }, [])

  const handleProjectUpdated = useCallback((updated: Project) => {
    setProjects(prev => prev.map(p =>
      p.project_id === updated.project_id ? mergeProject(p, updated) : p
    ))
    setSelected(prev =>
      prev?.project_id === updated.project_id ? mergeProject(prev, updated) : prev
    )
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (
        job.status === 'COMPLETED' &&
        [
          'process',
          'extract',
          'project',
          'knowledge_graph',
          'raw_workflow',
          'canonical_workflow',
          'workflow_maintenance',
          'workflow_maintenance_batch',
          'upload',
        ].includes(job.kind)
      ) {
        if (job.project_id && job.project_id !== '__upload__') {
          refreshProject(job.project_id).catch(() => load())
        } else {
          load()
        }
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load, refreshProject])

  const getStatus = useCallback(
    (id: string) => projects.find(p => p.project_id === id)?.frame_status ?? 'NO_FRAME',
    [projects],
  )

  if (loading) return <p className="text-slate-400 text-sm">Loading projects…</p>

  return (
    <>
      <ProjectGroupedList
        projects={projects}
        onProjectsChange={setProjects}
        onRefresh={load}
        spaces={spaces}
        getStatus={getStatus}
        emptyMessage="No projects yet — upload files in the Upload tab."
        columns={[
          { header: 'Assets', cellClassName: 'text-slate-400',
            render: p => p.asset_count },
          {
            header: 'Pipeline',
            headerClassName: 'w-28',
            cellClassName: 'w-28',
            render: p => (
              <StatusLights
                processed={p.processing_status ?? 'UNPROCESSED'}
                frame={p.frame_status ?? 'NO_FRAME'}
                workflow={p.workflow_status ?? 'NO_WORKFLOW'}
              />
            ),
          },
          { header: 'Created', cellClassName: 'text-slate-500 text-xs',
            render: p => p.created_at?.slice(0, 10) },
        ]}
        rowAction={p => (
          <button
            onClick={() => setSelected(p)}
            className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs"
          >
            Manage
          </button>
        )}
      />

      {selected && (
        <ProjectDetail
          project={selected}
          spaces={spaces}
          onClose={() => setSelected(null)}
          onJobComplete={() => refreshProject(selected.project_id)}
          onProjectUpdated={handleProjectUpdated}
          onDeleted={() => { setSelected(null); load() }}
        />
      )}
    </>
  )
}
