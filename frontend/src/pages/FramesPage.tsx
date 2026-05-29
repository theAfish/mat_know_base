import { useCallback, useEffect, useState } from 'react'

import { listFrames } from '../api/frames'
import { listProjects } from '../api/projects'
import { listSpaces } from '../api/spaces'
import ProjectDetail from '../components/frames/ProjectDetail'
import ProjectGroupedList from '../components/ProjectGroupedList'
import StatusBadge from '../components/StatusBadge'
import type { Project, Space } from '../types'


export default function FramesPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [frameMeta, setFrameMeta] = useState<Record<string, { version: number; extracted_at: string }>>({})
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Project | null>(null)
  const [spaces, setSpaces] = useState<Space[]>([])

  const load = useCallback(async () => {
    setLoading(prev => projects.length === 0 ? true : prev)
    try {
      const [pjs, frames] = await Promise.all([listProjects(5000), listFrames()])
      const meta: Record<string, { version: number; extracted_at: string }> = {}
      for (const f of frames) {
        meta[f.project_id] = {
          version: f.extraction_version ?? 0,
          extracted_at: (f.extracted_at ?? '').slice(0, 10) || '—',
        }
      }
      setProjects(pjs)
      setFrameMeta(meta)
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { listSpaces().then(setSpaces).catch(() => {}) }, [])

  const getStatus = useCallback(
    (id: string) => projects.find(p => p.project_id === id)?.frame_status ?? 'NO_FRAME',
    [projects],
  )

  const mergeProject = useCallback((updated: Project) => {
    setProjects(prev => prev.map(p => p.project_id === updated.project_id ? updated : p))
  }, [])

  if (selected) return (
    <ProjectDetail
      project={selected}
      onBack={() => setSelected(null)}
      onProjectUpdated={mergeProject}
    />
  )

  return (
    <div className="p-6 max-w-5xl">
      <h2 className="text-xl font-semibold mb-1">Knowledge Frames</h2>
      <p className="text-sm text-slate-400 mb-5">Browse and inspect extracted knowledge from each project.</p>
      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : (
        <ProjectGroupedList
          projects={projects}
          onProjectsChange={setProjects}
          onRefresh={load}
          spaces={spaces}
          getStatus={getStatus}
          emptyMessage="No projects yet — upload files in the Projects tab."
          columns={[
            { header: 'Processed',
              render: p => <StatusBadge status={p.processing_status ?? 'UNPROCESSED'} /> },
            { header: 'Frame',
              render: p => <StatusBadge status={p.frame_status ?? 'NO_FRAME'} /> },
            { header: 'Assets', cellClassName: 'text-slate-400',
              render: p => p.asset_count },
            { header: 'Version', cellClassName: 'text-slate-400',
              render: p => `v${frameMeta[p.project_id]?.version ?? 0}` },
            { header: 'Extracted', cellClassName: 'text-slate-500 text-xs',
              render: p => frameMeta[p.project_id]?.extracted_at ?? '—' },
          ]}
          rowAction={p => (
            <button onClick={() => setSelected(p)}
              className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs">
              View
            </button>
          )}
        />
      )}
    </div>
  )
}
