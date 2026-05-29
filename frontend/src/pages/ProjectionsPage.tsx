import type { JSX } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { startJobPolling } from '../api/jobPolling'
import { listProjectGroups } from '../api/projectGroups'
import { deleteProjection, exportProjections, listProjections, reviewProjections } from '../api/projections'
import { listProjects } from '../api/projects'
import { getSpace, listSpaces } from '../api/spaces'
import ProjectionRow from '../components/projections/ProjectionRow'
import SectionTable from '../components/projections/SectionTable'
import {
  GLOBAL_KG_SPACE,
  buildSectionRows,
  paperName,
} from '../components/projections/helpers'
import StatusBadge from '../components/StatusBadge'
import type { Job, Project, ProjectGroup, Projection, Space } from '../types'


export default function ProjectionsPage() {
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [spaceDetail, setSpaceDetail] = useState<Space | null>(null)
  const [projections, setProjections] = useState<Projection[]>([])
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [paperLookup, setPaperLookup] = useState<Record<string, string>>({})
  const [sectionRows, setSectionRows] = useState<Record<string, Array<Record<string, string>>>>({})
  const [loading, setLoading] = useState(false)
  const [newestOnly, setNewestOnly] = useState(true)
  const [showHistory, setShowHistory] = useState(false)
  const [reviewJob, setReviewJob] = useState<Job | null>(null)
  const [isReviewing, setIsReviewing] = useState(false)
  const [showSpaceDetail, setShowSpaceDetail] = useState(false)
  const [selectedProjectionIds, setSelectedProjectionIds] = useState<Set<string>>(new Set())
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [groupBy, setGroupBy] = useState(false)
  const [isExporting, setIsExporting] = useState(false)

  const toggleProjectionSelected = useCallback((id: string) => {
    setSelectedProjectionIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }, [])

  const selectAllProjections = useCallback(() => {
    setSelectedProjectionIds(new Set(projections.map(p => p.projection_id)))
  }, [projections])

  const clearProjectionSelection = useCallback(() => setSelectedProjectionIds(new Set()), [])

  const batchDeleteIds = useCallback(async (ids: string[]) => {
    if (ids.length === 0) return
    if (!confirm(`Delete ${ids.length} projection(s)? This cannot be undone.`)) return
    setBatchDeleting(true)
    try {
      const results = await Promise.allSettled(ids.map(id => deleteProjection(id)))
      const deletedOk = ids.filter((_, i) => results[i].status === 'fulfilled')
      const failed = results.length - deletedOk.length
      const okSet = new Set(deletedOk)
      setProjections(prev => prev.filter(p => !okSet.has(p.projection_id)))
      setSelectedProjectionIds(prev => {
        const next = new Set(prev)
        deletedOk.forEach(id => next.delete(id))
        return next
      })
      if (failed > 0) alert(`${failed} projection(s) failed to delete.`)
    } finally {
      setBatchDeleting(false)
    }
  }, [])

  useEffect(() => {
    listSpaces().then(sps => {
      const visible = sps.filter(s => s.name !== GLOBAL_KG_SPACE)
      setSpaces(visible)
      if (visible.length > 0) setSelectedSpaceId(visible[0].space_id)
    }).catch(() => {})
    listProjectGroups().then(setGroups).catch(() => {})
  }, [])

  const loadProjections = useCallback(async () => {
    if (!selectedSpaceId) return
    setLoading(true)
    try {
      const [projs, projects] = await Promise.all([
        listProjections({ space_id: selectedSpaceId, include_data: true, newest_only: newestOnly, include_history: showHistory, limit: 500 }),
        listProjects(500),
      ])
      const lookup: Record<string, string> = {}
      projects.forEach(p => {
        const name = paperName(p)
        if (name) lookup[p.project_id] = name
      })
      setPaperLookup(lookup)
      setAllProjects(projects)
      setProjections(projs)
      setSectionRows(buildSectionRows(projs, lookup))
      getSpace(selectedSpaceId).then(setSpaceDetail).catch(() => {})
    } finally {
      setLoading(false)
    }
  }, [selectedSpaceId, newestOnly, showHistory])

  useEffect(() => { loadProjections() }, [loadProjections])

  const [reviewMode, setReviewMode] = useState<'per_project' | 'session'>('per_project')

  const selectedProjectIds = useMemo(() => {
    if (selectedProjectionIds.size === 0) return [] as string[]
    const pids = new Set<string>()
    for (const proj of projections) {
      if (selectedProjectionIds.has(proj.projection_id) && proj.project_id) pids.add(proj.project_id)
    }
    return Array.from(pids)
  }, [selectedProjectionIds, projections])

  const pollJob = useCallback((jobId: string) => {
    startJobPolling({
      jobId,
      onUpdate: setReviewJob,
      onComplete: () => { setIsReviewing(false); loadProjections() },
      onFailed: () => setIsReviewing(false),
    })
  }, [loadProjections])

  const startReview = async (overrideProjectionIds?: string[]) => {
    try {
      setIsReviewing(true)
      const projectionIds = overrideProjectionIds && overrideProjectionIds.length > 0
        ? overrideProjectionIds
        : Array.from(selectedProjectionIds)
      const projectIds = (() => {
        if (projectionIds.length === 0) return [] as string[]
        const pids = new Set<string>()
        for (const proj of projections) {
          if (projectionIds.includes(proj.projection_id) && proj.project_id) pids.add(proj.project_id)
        }
        return Array.from(pids)
      })()
      const params: { space_id: string; project_ids?: string[]; mode: 'per_project' | 'session' } = {
        space_id: selectedSpaceId, mode: reviewMode,
      }
      if (projectIds.length > 0) params.project_ids = projectIds
      const { job_id } = await reviewProjections(params)
      pollJob(job_id)
    } catch { setIsReviewing(false) }
  }

  const exportSelected = useCallback(async (format: 'yaml' | 'json') => {
    const ids = Array.from(selectedProjectionIds)
    if (ids.length === 0) return
    setIsExporting(true)
    try {
      await exportProjections(ids, format)
    } catch (e) {
      alert(`Export failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setIsExporting(false)
    }
  }, [selectedProjectionIds])

  // Build section rows filtered to a specific set of project IDs (for grouped view)
  const groupedSectionRows = useMemo((): Array<{ label: string; color: string | null; rows: Record<string, Array<Record<string, string>>> }> => {
    if (!groupBy || groups.length === 0) return []
    const projectGroupMap: Record<string, string | null> = {}
    allProjects.forEach(p => { projectGroupMap[p.project_id] = p.group_id ?? null })

    const buckets: Array<{ group: ProjectGroup | null; projectIds: Set<string> }> = [
      ...groups.map(g => ({ group: g, projectIds: new Set<string>() })),
      { group: null, projectIds: new Set<string>() }, // Ungrouped
    ]
    allProjects.forEach(p => {
      const gid = p.group_id ?? null
      const bucket = gid ? buckets.find(b => b.group?.group_id === gid) : buckets[buckets.length - 1]
      if (bucket) bucket.projectIds.add(p.project_id)
    })

    return buckets
      .filter(b => b.projectIds.size > 0)
      .map(b => {
        const filteredProjections = projections.filter(p => b.projectIds.has(p.project_id))
        return {
          label: b.group?.name ?? 'Ungrouped',
          color: b.group?.color ?? null,
          rows: buildSectionRows(filteredProjections, paperLookup),
        }
      })
      .filter(b => Object.keys(b.rows).length > 0)
  }, [groupBy, groups, allProjects, projections, paperLookup])

  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const toggleGroupCollapsed = useCallback((label: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label); else next.add(label)
      return next
    })
  }, [])

  const userSpaces = spaces

  return (
    <div className="p-6 max-w-6xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Projections</h2>
          <p className="text-sm text-slate-400">Aggregated extraction results per space.</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">Mode:</label>
          <select
            value={reviewMode}
            onChange={e => setReviewMode(e.target.value as 'per_project' | 'session')}
            disabled={isReviewing}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-teal-500"
            title="per_project: separate reviewer session per project. session: one reviewer sees all selected projects."
          >
            <option value="per_project">Per project (default)</option>
            <option value="session">Single shared session</option>
          </select>
          <span
            className="text-xs text-slate-500"
            title="When projections are selected, only those projects are reviewed. Otherwise all projects in this space are reviewed (per_project only)."
          >
            {selectedProjectIds.length > 0
              ? `${selectedProjectIds.length} project(s) from selection`
              : reviewMode === 'session'
                ? 'select projections to enable'
                : 'all projects'}
          </span>
          <button
            onClick={() => startReview()}
            disabled={
              isReviewing
              || !selectedSpaceId
              || (reviewMode === 'session' && selectedProjectIds.length === 0)
            }
            className="px-3 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm font-medium"
          >
            {isReviewing ? 'Reviewing…' : '▶ Run Review'}
          </button>
        </div>
      </div>

      {userSpaces.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 text-sm text-slate-400">
          No spaces defined. Create a space first using the CLI.
        </div>
      ) : (
        <div className="bg-slate-800/60 border border-slate-700 rounded-xl p-3 space-y-2.5">
          {/* Row 1 — space selector + view toggles + utility actions */}
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Space</span>
              <select
                value={selectedSpaceId}
                onChange={e => setSelectedSpaceId(e.target.value)}
                className="bg-slate-700 border border-slate-600 rounded-lg px-2.5 py-1 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
              >
                {userSpaces.map(s => <option key={s.space_id} value={s.space_id}>{s.name}</option>)}
              </select>
            </div>
            <div className="h-4 w-px bg-slate-600/60 mx-0.5" />
            {/* View filter pills */}
            <div className="flex items-center gap-1.5">
              {([
                { key: 'newest', label: 'Newest only', active: newestOnly, toggle: () => setNewestOnly(v => !v) },
                { key: 'history', label: 'History', active: showHistory, toggle: () => setShowHistory(v => !v) },
                ...(groups.length > 0 ? [{ key: 'group', label: 'Group by', active: groupBy, toggle: () => setGroupBy(v => !v) }] : []),
              ] as { key: string; label: string; active: boolean; toggle: () => void }[]).map(({ key, label, active, toggle }) => (
                <button
                  key={key}
                  onClick={toggle}
                  className={`px-2.5 py-0.5 rounded-full text-xs font-medium border transition-colors ${
                    active
                      ? 'bg-teal-600/25 text-teal-300 border-teal-500/50'
                      : 'bg-slate-700/50 text-slate-400 border-slate-600/60 hover:border-slate-500 hover:text-slate-300'
                  }`}
                >{label}</button>
              ))}
            </div>
            <div className="flex-1" />
            <button
              onClick={loadProjections}
              title="Refresh"
              className="text-slate-500 hover:text-teal-400 transition-colors text-sm px-1.5"
            >↻ Refresh</button>
            {spaceDetail && (
              <button
                onClick={() => setShowSpaceDetail(s => !s)}
                className={`px-2.5 py-0.5 rounded-full text-xs border transition-colors ${
                  showSpaceDetail
                    ? 'bg-slate-700 border-slate-500 text-slate-300'
                    : 'border-slate-600/60 text-slate-500 hover:text-slate-300 hover:border-slate-500'
                }`}
              >{showSpaceDetail ? 'Hide details' : 'Space details'}</button>
            )}
          </div>
          {/* Row 2 — export / selection actions */}
          <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-slate-700/60">
            {selectedProjectionIds.size > 0 ? (
              <>
                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-teal-900/40 text-teal-300 border border-teal-700/50">
                  {selectedProjectionIds.size} selected
                </span>
                <button
                  onClick={() => exportSelected('yaml')}
                  disabled={isExporting}
                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border border-amber-600/50 text-amber-300 bg-amber-900/20 hover:bg-amber-800/30 disabled:opacity-40 transition-colors"
                  title="Export selected projections as YAML"
                >{isExporting ? 'Exporting…' : '⬇ YAML'}</button>
                <button
                  onClick={() => exportSelected('json')}
                  disabled={isExporting}
                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border border-amber-600/50 text-amber-300 bg-amber-900/20 hover:bg-amber-800/30 disabled:opacity-40 transition-colors"
                  title="Export selected projections as JSON"
                >{isExporting ? 'Exporting…' : '⬇ JSON'}</button>
                <button
                  onClick={clearProjectionSelection}
                  className="px-2.5 py-0.5 rounded-full text-xs border border-slate-600/60 text-slate-400 hover:text-slate-300 hover:border-slate-500 transition-colors"
                >Clear selection</button>
              </>
            ) : (
              <>
                <span className="text-[11px] text-slate-500">Export space:</span>
                <a
                  href={selectedSpaceId ? `/api/spaces/${selectedSpaceId}/export?format=yaml` : '#'}
                  onClick={e => { if (!selectedSpaceId) e.preventDefault() }}
                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border border-amber-600/40 text-amber-400 hover:bg-amber-900/20 transition-colors"
                  title="Download all projections for this space as a ZIP of YAML files"
                >⬇ YAML (all)</a>
                <a
                  href={selectedSpaceId ? `/api/spaces/${selectedSpaceId}/export?format=json` : '#'}
                  onClick={e => { if (!selectedSpaceId) e.preventDefault() }}
                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border border-amber-600/40 text-amber-400 hover:bg-amber-900/20 transition-colors"
                  title="Download all projections for this space as a ZIP of JSON files"
                >⬇ JSON (all)</a>
              </>
            )}
          </div>
        </div>
      )}

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

      {reviewJob && (
        <div className={`rounded-lg px-4 py-3 text-sm ${
          reviewJob.status === 'COMPLETED' ? 'bg-green-900/30 border border-green-700/50 text-green-200' :
          reviewJob.status === 'FAILED' ? 'bg-red-900/30 border border-red-700/50 text-red-200' :
          'bg-slate-800 border border-slate-700 text-slate-300'
        }`}>
          {isReviewing && <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin mr-2" />}
          <StatusBadge status={reviewJob.status} />
          <span className="ml-2">{reviewJob.current_message || reviewJob.status}</span>
        </div>
      )}

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : projections.length === 0 ? (
        <p className="text-slate-400 text-sm">No projections for this space yet.</p>
      ) : (
        <div className="space-y-6">
          <div>
            <h3 className="text-base font-semibold text-slate-200 mb-3">
              All Extracted Data — {projections.length} projection(s)
            </h3>
            {Object.keys(sectionRows).length === 0 ? (
              <p className="text-sm text-slate-400">No completed projection data available yet.</p>
            ) : groupBy && groupedSectionRows.length > 0 ? (
              <div className="space-y-4">
                {groupedSectionRows.map(({ label, color, rows }) => {
                  const isCollapsed = collapsedGroups.has(label)
                  const totalRows = Object.values(rows).reduce((s, r) => s + r.length, 0)
                  return (
                    <div key={label} className="border border-slate-700 rounded-xl overflow-hidden">
                      <button
                        onClick={() => toggleGroupCollapsed(label)}
                        className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-slate-800/70 hover:bg-slate-700/60 transition-colors group"
                      >
                        {color
                          ? <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: color }} />
                          : <span className="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-slate-600" />}
                        <h4 className="text-sm font-semibold text-slate-200 group-hover:text-white">{label}</h4>
                        <span className="text-[11px] text-slate-500">{totalRows} row{totalRows !== 1 ? 's' : ''}</span>
                        <span className="ml-auto text-slate-500 text-xs transition-transform" style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}>▾</span>
                      </button>
                      {!isCollapsed && (
                        <div className="space-y-4 p-4">
                          {Object.entries(rows).map(([section, sectionRowData]) => {
                            const sectionSchema = spaceDetail?.extraction_schema?.[section] as Record<string, unknown> | undefined
                            const itemSchema = sectionSchema?.item_schema as Record<string, unknown> | undefined
                            const schemaOrder = itemSchema ? Object.keys(itemSchema) : undefined
                            return (
                              <SectionTable
                                key={`${label}::${section}`}
                                name={section}
                                rows={sectionRowData}
                                schemaOrder={schemaOrder}
                                onRequestDeleteProjection={batchDeleteIds}
                                onRequestReview={ids => startReview(ids)}
                                reviewDisabled={isReviewing || !selectedSpaceId}
                                selectedProjectionIds={selectedProjectionIds}
                                onToggleProjection={toggleProjectionSelected}
                                onClearSelection={clearProjectionSelection}
                              />
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="space-y-6">
                {Object.entries(sectionRows).map(([section, rows]) => {
                  const sectionSchema = spaceDetail?.extraction_schema?.[section] as Record<string, unknown> | undefined
                  const itemSchema = sectionSchema?.item_schema as Record<string, unknown> | undefined
                  const schemaOrder = itemSchema ? Object.keys(itemSchema) : undefined
                  return (
                    <SectionTable
                      key={section}
                      name={section}
                      rows={rows}
                      schemaOrder={schemaOrder}
                      onRequestDeleteProjection={batchDeleteIds}
                      onRequestReview={ids => startReview(ids)}
                      reviewDisabled={isReviewing || !selectedSpaceId}
                      selectedProjectionIds={selectedProjectionIds}
                      onToggleProjection={toggleProjectionSelected}
                      onClearSelection={clearProjectionSelection}
                    />
                  )
                })}
              </div>
            )}
          </div>

          <div>
            <div className="flex items-center gap-2 flex-wrap mb-3 pt-2 border-t border-slate-700">
              <h3 className="text-base font-semibold text-slate-200">Individual Projections</h3>
              <button onClick={selectAllProjections}
                className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200">
                Select all ({projections.length})
              </button>
              {selectedProjectionIds.size > 0 && (
                <>
                  <span className="text-[11px] text-slate-400">{selectedProjectionIds.size} selected</span>
                  <button onClick={clearProjectionSelection}
                    className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300">Clear</button>
                  <button onClick={() => batchDeleteIds(Array.from(selectedProjectionIds))} disabled={batchDeleting}
                    className="text-[11px] px-2 py-0.5 rounded bg-red-900/50 hover:bg-red-800/70 text-red-200 border border-red-700/40 disabled:opacity-40">
                    {batchDeleting ? 'Deleting…' : `Delete ${selectedProjectionIds.size} selected`}
                  </button>
                </>
              )}
            </div>
            <div className="space-y-1">
              {showHistory
                ? (() => {
                    const byId = new Map(projections.map(p => [p.projection_id, p]))
                    const live = projections.filter(p => !p.superseded_by_id)
                    const renderChain = (root: Projection): JSX.Element => {
                      const ancestors: Projection[] = []
                      const walk = (ids: string[] | null | undefined) => {
                        if (!ids) return
                        for (const id of ids) {
                          const anc = byId.get(id)
                          if (anc) { ancestors.push(anc); walk(anc.supersedes_ids) }
                        }
                      }
                      walk(root.supersedes_ids)
                      const onDeleted = (id: string) => {
                        setProjections(prev => prev.filter(x => x.projection_id !== id))
                        setSelectedProjectionIds(prev => { const n = new Set(prev); n.delete(id); return n })
                      }
                      return (
                        <div key={root.projection_id} className="space-y-0.5">
                          <ProjectionRow
                            proj={root} paperLookup={paperLookup}
                            selected={selectedProjectionIds.has(root.projection_id)}
                            onToggleSelected={toggleProjectionSelected}
                            onDeleted={onDeleted}
                          />
                          {ancestors.map((anc, i) => (
                            <div key={anc.projection_id} className="ml-6 border-l-2 border-slate-600/50 pl-2">
                              <div className="flex items-center gap-1 px-2 py-0.5">
                                <span className="text-[10px] text-slate-500 font-mono">
                                  {i === 0 ? '↳ supersedes' : '  ↳'}
                                </span>
                              </div>
                              <ProjectionRow
                                proj={anc} paperLookup={paperLookup}
                                selected={selectedProjectionIds.has(anc.projection_id)}
                                onToggleSelected={toggleProjectionSelected}
                                onDeleted={onDeleted}
                              />
                            </div>
                          ))}
                        </div>
                      )
                    }
                    return live.map(renderChain)
                  })()
                : projections.map(p => (
                    <ProjectionRow
                      key={p.projection_id}
                      proj={p}
                      paperLookup={paperLookup}
                      selected={selectedProjectionIds.has(p.projection_id)}
                      onToggleSelected={toggleProjectionSelected}
                      onDeleted={id => {
                        setProjections(prev => prev.filter(x => x.projection_id !== id))
                        setSelectedProjectionIds(prev => {
                          const next = new Set(prev)
                          next.delete(id)
                          return next
                        })
                      }}
                    />
                  ))
              }
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
