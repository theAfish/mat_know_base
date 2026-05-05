import { useState, useCallback, useEffect } from 'react'
import { listProjections } from '../api/projections'
import { listSpaces, getSpace } from '../api/spaces'
import { listProjects } from '../api/projects'
import { getJob } from '../api/jobs'
import StatusBadge from '../components/StatusBadge'
import type { Projection, Space, Project, Job } from '../types'

const GLOBAL_KG_SPACE = '__global_kg__'
const PAGE_SIZE = 50

// ─── Helpers ──────────────────────────────────────────────────────────────────

function paperName(project: Project | undefined): string {
  if (!project) return ''
  const src = project.source_path ?? ''
  if (!src) return project.label ?? project.project_id.slice(0, 12)
  const parts = src.replace(/[/\\]+$/, '').split(/[/\\]/)
  return parts[parts.length - 1] || project.label || project.project_id.slice(0, 12)
}

function stringify(val: unknown): string {
  if (val === null || val === undefined) return ''
  if (typeof val === 'string') return val
  if (typeof val === 'boolean' || typeof val === 'number') return String(val)
  if (Array.isArray(val)) return val.map(stringify).join(', ')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

// ID-like column heuristic
function defaultColumns(cols: string[]): string[] {
  const idCols = new Set(cols.filter(c =>
    c.toLowerCase() === 'id' || c.toLowerCase().endsWith('_id') || c.toLowerCase().includes('_id_')
  ))
  const priority = ['paper_name', 'source_paper_name', 'is_core_study_data', 'extracted_at']
    .filter(c => cols.includes(c))
  const rest = cols.filter(c => !priority.includes(c) && !idCols.has(c))
  const visible = [...priority, ...rest]
  return visible.length > 0 ? visible : cols
}

// Build combined section→rows from all projections
function buildSectionRows(
  projections: Projection[],
  paperLookup: Record<string, string>,
): Record<string, Array<Record<string, string>>> {
  const sectionRows: Record<string, Array<Record<string, string>>> = {}

  for (const proj of projections) {
    if (!['COMPLETED', 'REVIEWED'].includes(proj.status) || !proj.data) continue
    const pname = paperLookup[proj.project_id] ?? ''
    const meta = {
      project_id: proj.project_id,
      projection_id: proj.projection_id,
      extracted_at: (proj.extracted_at ?? '').slice(0, 10),
      ...(pname ? { paper_name: pname } : {}),
    }

    for (const [section, value] of Object.entries(proj.data)) {
      const rows = sectionToRows(value, meta, paperLookup)
      if (rows.length > 0) {
        sectionRows[section] = [...(sectionRows[section] ?? []), ...rows]
      }
    }
  }
  return sectionRows
}

function sectionToRows(
  value: unknown,
  meta: Record<string, string>,
  paperLookup: Record<string, string>,
): Array<Record<string, string>> {
  if (Array.isArray(value)) {
    if (value.length === 0) return []
    if (value.every(v => typeof v === 'object' && v !== null && !Array.isArray(v))) {
      const rows = (value as Record<string, unknown>[]).map(item => {
        const row: Record<string, string> = { ...meta }
        for (const [k, v] of Object.entries(item)) row[k] = stringify(v)
        // enrich source_project_id → source_paper_name
        if (row.source_project_id && paperLookup[row.source_project_id]) {
          row.source_paper_name = row.source_paper_name ?? paperLookup[row.source_project_id]
        }
        return row
      })
      // sort: core study data first
      return rows.sort((a, b) => {
        const av = String(a.is_core_study_data ?? '').toLowerCase()
        const bv = String(b.is_core_study_data ?? '').toLowerCase()
        const aCore = ['true', '1', 'yes'].includes(av)
        const bCore = ['true', '1', 'yes'].includes(bv)
        return (bCore ? 1 : 0) - (aCore ? 1 : 0)
      })
    }
    return value.map(v => ({ ...meta, value: stringify(v) }))
  }
  if (typeof value === 'object' && value !== null) {
    return [{ ...meta, ...Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, stringify(v)])) }]
  }
  return [{ ...meta, value: stringify(value) }]
}

// ─── Combined table for one section ──────────────────────────────────────────

function SectionTable({
  name,
  rows,
}: {
  name: string
  rows: Array<Record<string, string>>
}) {
  const [page, setPage] = useState(1)
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const start = (page - 1) * PAGE_SIZE
  const pageRows = rows.slice(start, start + PAGE_SIZE)
  const cols = rows.length > 0 ? defaultColumns(Object.keys(rows[0])) : []

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-slate-200 capitalize">
        {name.replace(/_/g, ' ')} ({rows.length})
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              {cols.map(col => (
                <th key={col} className="text-left px-2 py-1.5 text-slate-400 font-medium whitespace-nowrap">
                  {col.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {pageRows.map((row, i) => (
              <tr key={i} className="hover:bg-slate-800/40">
                {cols.map(col => (
                  <td key={col} className="px-2 py-1.5 text-slate-300 max-w-xs truncate" title={row[col]}>
                    {row[col] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded">
            ‹
          </button>
          <span>Page {page} / {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded">
            ›
          </button>
          <span className="ml-1 text-slate-500">
            (rows {start + 1}–{Math.min(rows.length, start + PAGE_SIZE)} of {rows.length})
          </span>
        </div>
      )}
    </div>
  )
}

// ─── Individual projection list item ─────────────────────────────────────────

function ProjectionRow({
  proj,
  paperLookup,
}: {
  proj: Projection
  paperLookup: Record<string, string>
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(s => !s)}
        className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-slate-700/50"
      >
        <StatusBadge status={proj.status} />
        <span className="flex-1 text-sm text-slate-300 truncate">
          {paperLookup[proj.project_id] ?? proj.project_id.slice(0, 12)}
        </span>
        <span className="text-xs text-slate-500">
          {proj.times_reviewed > 0 ? `Reviewed ${proj.times_reviewed}×` : 'Raw'}
          {' · '}v{proj.space_version}
          {proj.extracted_at ? ` · ${proj.extracted_at.slice(0, 10)}` : ''}
        </span>
        <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-slate-700 space-y-3">
          {proj.agent_notes && (
            <p className="text-xs text-slate-400 italic">{proj.agent_notes.slice(0, 300)}{proj.agent_notes.length > 300 ? '…' : ''}</p>
          )}
          {proj.review_notes && (
            <div className="bg-teal-900/30 border border-teal-700/40 rounded px-3 py-2 text-xs text-teal-200">
              {proj.review_notes.slice(0, 200)}
            </div>
          )}
          {proj.data && Object.entries(proj.data).map(([section, items]) => {
            const rows = Array.isArray(items) ? items : []
            return rows.length > 0 ? (
              <div key={section} className="space-y-1">
                <p className="text-xs font-medium text-slate-400 capitalize">{section.replace(/_/g, ' ')} ({rows.length})</p>
                {rows.slice(0, 3).map((item, i) => (
                  <div key={i} className="text-xs text-slate-500 pl-2 truncate">
                    {typeof item === 'object' && item !== null
                      ? Object.entries(item as Record<string, unknown>).slice(0, 3).map(([k, v]) =>
                          `${k}: ${stringify(v)}`).join(' · ')
                      : stringify(item)
                    }
                  </div>
                ))}
                {rows.length > 3 && <p className="text-xs text-slate-600 pl-2">…and {rows.length - 3} more</p>}
              </div>
            ) : null
          })}
        </div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ProjectionsPage() {
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [spaceDetail, setSpaceDetail] = useState<Space | null>(null)
  const [projections, setProjections] = useState<Projection[]>([])
  const [paperLookup, setPaperLookup] = useState<Record<string, string>>({})
  const [sectionRows, setSectionRows] = useState<Record<string, Array<Record<string, string>>>>({})
  const [loading, setLoading] = useState(false)
  const [newestOnly, setNewestOnly] = useState(true)
  const [reviewJob, setReviewJob] = useState<Job | null>(null)
  const [isReviewing, setIsReviewing] = useState(false)
  const [showSpaceDetail, setShowSpaceDetail] = useState(false)

  // Load spaces once
  useEffect(() => {
    listSpaces().then(sps => {
      const visible = sps.filter(s => s.name !== GLOBAL_KG_SPACE)
      setSpaces(visible)
      if (visible.length > 0) setSelectedSpaceId(visible[0].space_id)
    }).catch(() => {})
  }, [])

  // Load projections when space changes
  const loadProjections = useCallback(async () => {
    if (!selectedSpaceId) return
    setLoading(true)
    try {
      const [projs, projects] = await Promise.all([
        listProjections({ space_id: selectedSpaceId, include_data: true, newest_only: newestOnly, limit: 500 }),
        listProjects(500),
      ])

      // Build paper name lookup from projects
      const lookup: Record<string, string> = {}
      projects.forEach(p => {
        const name = paperName(p)
        if (name) lookup[p.project_id] = name
      })
      setPaperLookup(lookup)
      setProjections(projs)
      setSectionRows(buildSectionRows(projs, lookup))

      // Load space detail
      getSpace(selectedSpaceId).then(setSpaceDetail).catch(() => {})
    } finally {
      setLoading(false)
    }
  }, [selectedSpaceId, newestOnly])

  useEffect(() => { loadProjections() }, [loadProjections])

  const { reviewProjections } = (() => {
    // inline import to avoid circular dep
    const reviewProjections = async () => {
      const { reviewProjections: fn } = await import('../api/projections')
      return fn({ space_id: selectedSpaceId })
    }
    return { reviewProjections }
  })()

  const startReview = async () => {
    try {
      setIsReviewing(true)
      const { job_id } = await reviewProjections()
      pollJob(job_id)
    } catch { setIsReviewing(false) }
  }

  const pollJob = (jobId: string) => {
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        setReviewJob(job)
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          setTimeout(poll, 1000)
        } else {
          setIsReviewing(false)
          if (job.status === 'COMPLETED') loadProjections()
        }
      } catch { setTimeout(poll, 2000) }
    }
    setTimeout(poll, 500)
  }

  const userSpaces = spaces
  const selectedSpace = spaces.find(s => s.space_id === selectedSpaceId)

  return (
    <div className="p-6 max-w-6xl space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Projections</h2>
          <p className="text-sm text-slate-400">Aggregated extraction results per space.</p>
        </div>
        <button
          onClick={startReview}
          disabled={isReviewing || !selectedSpaceId}
          className="px-3 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm font-medium"
        >
          {isReviewing ? 'Reviewing…' : '▶ Run Review'}
        </button>
      </div>

      {/* Space selector */}
      {userSpaces.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 text-sm text-slate-400">
          No spaces defined. Create a space first using the CLI.
        </div>
      ) : (
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm text-slate-400">Space:</label>
          <select
            value={selectedSpaceId}
            onChange={e => setSelectedSpaceId(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
          >
            {userSpaces.map(s => <option key={s.space_id} value={s.space_id}>{s.name}</option>)}
          </select>
          <label className="text-sm text-slate-400 ml-2">
            <input
              type="checkbox"
              checked={newestOnly}
              onChange={e => setNewestOnly(e.target.checked)}
              className="mr-1.5"
            />
            Newest only
          </label>
          <button onClick={loadProjections} className="text-xs text-teal-400 hover:text-teal-300">Refresh</button>
          {spaceDetail && (
            <button onClick={() => setShowSpaceDetail(s => !s)} className="text-xs text-slate-400 hover:text-slate-300 ml-auto">
              {showSpaceDetail ? 'Hide space details' : 'Space details'}
            </button>
          )}
        </div>
      )}

      {/* Space detail */}
      {showSpaceDetail && spaceDetail && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 text-sm space-y-1">
          <p><span className="text-slate-400">Domain:</span> <span className="text-slate-200">{spaceDetail.domain}</span></p>
          {spaceDetail.description && <p className="text-slate-400 text-xs">{spaceDetail.description}</p>}
          <details className="mt-2">
            <summary className="text-xs text-teal-400 cursor-pointer">Schema</summary>
            <pre className="mt-1 text-xs text-slate-500 overflow-x-auto max-h-48">{JSON.stringify(spaceDetail.extraction_schema, null, 2)}</pre>
          </details>
        </div>
      )}

      {/* Review job status */}
      {reviewJob && (
        <div className={`rounded-lg px-4 py-3 text-sm ${
          reviewJob.status === 'COMPLETED' ? 'bg-green-900/30 border border-green-700/50 text-green-200' :
          reviewJob.status === 'FAILED' ? 'bg-red-900/30 border border-red-700/50 text-red-200' :
          'bg-slate-800 border border-slate-700 text-slate-300'
        }`}>
          {isReviewing && <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin mr-2" />}
          {reviewJob.current_message || reviewJob.status}
        </div>
      )}

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : projections.length === 0 ? (
        <p className="text-slate-400 text-sm">No projections for this space yet.</p>
      ) : (
        <div className="space-y-6">
          {/* ── Combined aggregated table ── */}
          <div>
            <h3 className="text-base font-semibold text-slate-200 mb-3">
              All Extracted Data — {projections.length} projection(s)
            </h3>
            {Object.keys(sectionRows).length === 0 ? (
              <p className="text-sm text-slate-400">No completed projection data available yet.</p>
            ) : (
              <div className="space-y-6">
                {Object.entries(sectionRows).map(([section, rows]) => (
                  <SectionTable key={section} name={section} rows={rows} />
                ))}
              </div>
            )}
          </div>

          {/* ── Individual projection list ── */}
          <div>
            <h3 className="text-base font-semibold text-slate-200 mb-3 pt-2 border-t border-slate-700">
              Individual Projections
            </h3>
            <div className="space-y-1">
              {projections.map(p => (
                <ProjectionRow key={p.projection_id} proj={p} paperLookup={paperLookup} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
