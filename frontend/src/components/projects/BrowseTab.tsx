import { useCallback, useEffect, useState } from 'react'

import { listProjects } from '../../api/projects'
import ProjectGroupedList from '../ProjectGroupedList'
import StatusBadge from '../StatusBadge'
import type { Project, Space } from '../../types'
import ProjectDetail from './ProjectDetail'


export default function BrowseTab({ spaces }: { spaces: Space[] }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Project | null>(null)

  const load = useCallback(async () => {
    try {
      // Don't blank the table during a background refresh — only show the
      // loading placeholder on the very first fetch.
      setLoading(prev => projects.length === 0 ? true : prev)
      const data = await listProjects(5000)
      setProjects(data)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { load() }, [load])

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
          { header: 'Processed',
            render: p => <StatusBadge status={p.processing_status ?? 'UNPROCESSED'} /> },
          { header: 'Frame',
            render: p => <StatusBadge status={p.frame_status ?? 'NO_FRAME'} /> },
          { header: 'Workflow',
            render: p => <StatusBadge status={p.workflow_status ?? 'NO_WORKFLOW'} /> },
          { header: 'Normalized',
            render: p => <StatusBadge status={p.canonical_workflow_status ?? 'NO_CANONICAL_WORKFLOW'} /> },
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
          onJobComplete={load}
          onProjectUpdated={updated => setProjects(prev => prev.map(p =>
            p.project_id === updated.project_id ? updated : p
          ))}
          onDeleted={() => { setSelected(null); load() }}
        />
      )}
    </>
  )
}
