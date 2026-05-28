import { useState, useCallback, useEffect, useRef, useMemo, useLayoutEffect } from 'react'
import { listProjections, deleteProjection } from '../api/projections'
import { listSpaces, getSpace } from '../api/spaces'
import { listProjects } from '../api/projects'
import { getJob } from '../api/jobs'
import { nextJobPollDelayMs, shouldStopJobPolling } from '../api/jobPolling'
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
// schemaOrder: keys from item_schema (preserves the schema author's intended order)
function defaultColumns(cols: string[], schemaOrder?: string[]): string[] {
  const idCols = new Set(cols.filter(c =>
    c.toLowerCase() === 'id' || c.toLowerCase().endsWith('_id') || c.toLowerCase().includes('_id_')
  ))
  const metaCols = ['paper_name', 'source_paper_name', 'is_core_study_data', 'extracted_at']
    .filter(c => cols.includes(c))
  if (schemaOrder && schemaOrder.length > 0) {
    // schema-defined fields first (in schema order), then meta cols, then anything else
    const schemaPresent = schemaOrder.filter(c => cols.includes(c))
    const rest = cols.filter(c => !schemaPresent.includes(c) && !metaCols.includes(c) && !idCols.has(c))
    const visible = [...schemaPresent, ...metaCols, ...rest]
    return visible.length > 0 ? visible : cols
  }
  const rest = cols.filter(c => !metaCols.includes(c) && !idCols.has(c))
  const visible = [...metaCols, ...rest]
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

// ─── Column preferences (per section, persisted to localStorage) ─────────────

type ColPrefs = {
  // Ordered list of visible columns (subset of all known cols).
  visible: string[]
  // Per-column width (px). Missing = auto.
  widths: Record<string, number>
  // All columns ever seen (so we can offer hidden ones in the picker).
  known: string[]
}

const COL_PREFS_KEY = (name: string) => `mkb_proj_cols::${name}`

function loadColPrefs(name: string): ColPrefs | null {
  try {
    const raw = localStorage.getItem(COL_PREFS_KEY(name))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || !Array.isArray(parsed.visible)) return null
    return {
      visible: parsed.visible,
      widths: parsed.widths ?? {},
      known: Array.isArray(parsed.known) ? parsed.known : parsed.visible,
    }
  } catch {
    return null
  }
}

function saveColPrefs(name: string, prefs: ColPrefs) {
  try { localStorage.setItem(COL_PREFS_KEY(name), JSON.stringify(prefs)) } catch { /* ignore quota */ }
}

// ─── Combined table for one section ──────────────────────────────────────────

function SectionTable({
  name,
  rows,
  schemaOrder,
  onRequestDeleteProjection,
}: {
  name: string
  rows: Array<Record<string, string>>
  schemaOrder?: string[]
  onRequestDeleteProjection?: (projectionIds: string[]) => void
}) {
  const [page, setPage] = useState(1)
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const start = (page - 1) * PAGE_SIZE
  const pageRows = rows.slice(start, start + PAGE_SIZE)

  // Discover every column across all rows so hidden cols remain pickable.
  const allCols = useMemo(() => {
    const seen = new Set<string>()
    for (const r of rows) for (const k of Object.keys(r)) seen.add(k)
    return Array.from(seen)
  }, [rows])

  // Initial / merged column preferences
  const [prefs, setPrefs] = useState<ColPrefs>(() => {
    const saved = loadColPrefs(name)
    const defaults = defaultColumns(allCols.length > 0 ? allCols : [], schemaOrder)
    if (!saved) return { visible: defaults, widths: {}, known: allCols }
    // Merge: keep saved ordering, append any newly discovered cols at end (hidden)
    const merged = { ...saved, known: Array.from(new Set([...saved.known, ...allCols])) }
    return merged
  })

  // When rows change and new columns appear, fold them into `known`.
  useEffect(() => {
    setPrefs(p => {
      const merged = Array.from(new Set([...p.known, ...allCols]))
      if (merged.length === p.known.length) return p
      const next = { ...p, known: merged }
      saveColPrefs(name, next)
      return next
    })
  }, [allCols, name])

  const visibleCols = prefs.visible.filter(c => prefs.known.includes(c))

  // Persist on change
  const updatePrefs = (fn: (p: ColPrefs) => ColPrefs) =>
    setPrefs(p => {
      const next = fn(p)
      saveColPrefs(name, next)
      return next
    })

  // ── Cell expansion modal ────────────────────────────────────────────────
  const [expandedCell, setExpandedCell] = useState<{ col: string; value: string } | null>(null)

  // ── Selection (row-level → maps to projection_id) ───────────────────────
  const [selectedRowKeys, setSelectedRowKeys] = useState<Set<string>>(new Set())
  const rowKey = (row: Record<string, string>, idx: number) =>
    row.projection_id || row.id || `${start + idx}`
  const pageRowKeys = pageRows.map(rowKey)
  const allPageSelected = pageRowKeys.length > 0 && pageRowKeys.every(k => selectedRowKeys.has(k))
  const togglePageAll = () => {
    setSelectedRowKeys(prev => {
      const next = new Set(prev)
      if (allPageSelected) pageRowKeys.forEach(k => next.delete(k))
      else pageRowKeys.forEach(k => next.add(k))
      return next
    })
  }
  const toggleRow = (k: string) => {
    setSelectedRowKeys(prev => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k); else next.add(k)
      return next
    })
  }
  // Unique projection ids implicated by selection
  const selectedProjectionIds = useMemo(() => {
    const ids = new Set<string>()
    for (const r of rows) {
      const k = r.projection_id || r.id
      if (k && selectedRowKeys.has(k) && r.projection_id) ids.add(r.projection_id)
    }
    return Array.from(ids)
  }, [rows, selectedRowKeys])

  // ── Floating bottom scrollbar (always reachable, even when the table
  //    is tall and the native scrollbar sits below the viewport) ──────────
  const floatingScrollRef = useRef<HTMLDivElement>(null)
  const bodyScrollRef = useRef<HTMLDivElement>(null)
  const [innerWidth, setInnerWidth] = useState(0)
  const [floating, setFloating] = useState<{ visible: boolean; left: number; width: number }>({
    visible: false, left: 0, width: 0,
  })

  useLayoutEffect(() => {
    const el = bodyScrollRef.current
    if (!el) return
    const update = () => setInnerWidth(el.scrollWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [pageRows, visibleCols, prefs.widths])

  useEffect(() => {
    const recompute = () => {
      const el = bodyScrollRef.current
      if (!el) { setFloating(s => s.visible ? { ...s, visible: false } : s); return }
      const r = el.getBoundingClientRect()
      const vh = window.innerHeight
      const overflows = el.scrollWidth > el.clientWidth + 1
      // Hide the floating bar when the native one is already on-screen.
      const nativeVisible = r.bottom <= vh
      const partlyOnScreen = r.bottom > 0 && r.top < vh
      const visible = overflows && partlyOnScreen && !nativeVisible
      setFloating(prev =>
        prev.visible === visible &&
        Math.round(prev.left) === Math.round(r.left) &&
        Math.round(prev.width) === Math.round(r.width)
          ? prev
          : { visible, left: r.left, width: r.width },
      )
    }
    recompute()
    window.addEventListener('scroll', recompute, true)
    window.addEventListener('resize', recompute)
    return () => {
      window.removeEventListener('scroll', recompute, true)
      window.removeEventListener('resize', recompute)
    }
  }, [innerWidth])

  const onFloatingScroll = (e: React.UIEvent<HTMLDivElement>) => {
    if (bodyScrollRef.current && bodyScrollRef.current.scrollLeft !== e.currentTarget.scrollLeft)
      bodyScrollRef.current.scrollLeft = e.currentTarget.scrollLeft
  }
  const onBodyScroll = (e: React.UIEvent<HTMLDivElement>) => {
    if (floatingScrollRef.current && floatingScrollRef.current.scrollLeft !== e.currentTarget.scrollLeft)
      floatingScrollRef.current.scrollLeft = e.currentTarget.scrollLeft
  }

  // ── Column picker popover ───────────────────────────────────────────────
  const [showPicker, setShowPicker] = useState(false)
  const moveCol = (col: string, dir: -1 | 1) => updatePrefs(p => {
    const idx = p.visible.indexOf(col)
    if (idx < 0) return p
    const swap = idx + dir
    if (swap < 0 || swap >= p.visible.length) return p
    const next = [...p.visible]
    ;[next[idx], next[swap]] = [next[swap], next[idx]]
    return { ...p, visible: next }
  })
  const toggleColVisible = (col: string) => updatePrefs(p => {
    if (p.visible.includes(col)) return { ...p, visible: p.visible.filter(c => c !== col) }
    return { ...p, visible: [...p.visible, col] }
  })
  const setColWidth = (col: string, w: number | null) => updatePrefs(p => {
    const widths = { ...p.widths }
    if (w == null || Number.isNaN(w) || w <= 0) delete widths[col]
    else widths[col] = Math.max(40, Math.min(1200, Math.round(w)))
    return { ...p, widths }
  })
  const resetPrefs = () => updatePrefs(() => ({
    visible: defaultColumns(allCols, schemaOrder),
    widths: {},
    known: allCols,
  }))

  const colStyle = (col: string): React.CSSProperties => {
    const w = prefs.widths[col]
    if (!w) return { maxWidth: '20rem' }
    return { width: w, minWidth: w, maxWidth: w }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <h4 className="text-sm font-semibold text-slate-200 capitalize">
          {name.replace(/_/g, ' ')} ({rows.length})
        </h4>
        <button
          onClick={() => setShowPicker(s => !s)}
          className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
          title="Show/hide, reorder, and resize columns"
        >
          ⚙ Columns ({visibleCols.length}/{prefs.known.length})
        </button>
        {selectedRowKeys.size > 0 && (
          <>
            <span className="text-[11px] text-slate-400">
              {selectedRowKeys.size} row(s) · {selectedProjectionIds.length} projection(s) selected
            </span>
            <button
              onClick={() => setSelectedRowKeys(new Set())}
              className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
            >
              Clear
            </button>
            {onRequestDeleteProjection && selectedProjectionIds.length > 0 && (
              <button
                onClick={() => {
                  onRequestDeleteProjection(selectedProjectionIds)
                  setSelectedRowKeys(new Set())
                }}
                className="text-[11px] px-2 py-0.5 rounded bg-red-900/50 hover:bg-red-800/70 text-red-200 border border-red-700/40"
                title="Delete every projection that contributed any selected row"
              >
                Delete {selectedProjectionIds.length} projection(s)
              </button>
            )}
          </>
        )}
      </div>

      {showPicker && (
        <div className="bg-slate-900 border border-slate-700 rounded p-3 text-xs space-y-2 max-h-72 overflow-y-auto">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">Column controls</span>
            <button onClick={resetPrefs} className="text-teal-400 hover:text-teal-300">Reset defaults</button>
          </div>
          <div className="grid grid-cols-1 gap-1">
            {/* Visible cols first (in order), then hidden */}
            {[...prefs.visible, ...prefs.known.filter(c => !prefs.visible.includes(c))].map(col => {
              const isVisible = prefs.visible.includes(col)
              return (
                <div key={col} className="flex items-center gap-2 py-0.5">
                  <input
                    type="checkbox"
                    checked={isVisible}
                    onChange={() => toggleColVisible(col)}
                    className="accent-teal-500"
                  />
                  <span className={`flex-1 truncate ${isVisible ? 'text-slate-200' : 'text-slate-500'}`}>
                    {col.replace(/_/g, ' ')}
                  </span>
                  {isVisible && (
                    <>
                      <button
                        onClick={() => moveCol(col, -1)}
                        className="px-1 text-slate-500 hover:text-slate-200"
                        title="Move left"
                      >↑</button>
                      <button
                        onClick={() => moveCol(col, 1)}
                        className="px-1 text-slate-500 hover:text-slate-200"
                        title="Move right"
                      >↓</button>
                      <input
                        type="number"
                        placeholder="auto"
                        value={prefs.widths[col] ?? ''}
                        onChange={e => setColWidth(col, e.target.value === '' ? null : Number(e.target.value))}
                        className="w-16 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-slate-200"
                        title="Width in pixels (blank = auto)"
                      />
                      <span className="text-slate-600">px</span>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Body scroll container (native horizontal scrollbar lives here) */}
      <div ref={bodyScrollRef} onScroll={onBodyScroll} className="overflow-x-auto">
        <table className="text-xs border-collapse" style={{ minWidth: '100%' }}>
          <thead>
            <tr className="border-b border-slate-700">
              <th className="px-2 py-1.5 w-8">
                <input
                  type="checkbox"
                  checked={allPageSelected}
                  onChange={togglePageAll}
                  className="accent-teal-500"
                  title="Select all rows on this page"
                />
              </th>
              {visibleCols.map(col => (
                <th
                  key={col}
                  className="text-left px-2 py-1.5 text-slate-400 font-medium whitespace-nowrap"
                  style={colStyle(col)}
                >
                  {col.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {pageRows.map((row, i) => {
              const k = rowKey(row, i)
              const selected = selectedRowKeys.has(k)
              return (
                <tr key={k} className={selected ? 'bg-teal-900/20' : 'hover:bg-slate-800/40'}>
                  <td className="px-2 py-1.5 w-8 align-top">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleRow(k)}
                      className="accent-teal-500"
                    />
                  </td>
                  {visibleCols.map(col => {
                    const v = row[col] ?? ''
                    const long = v.length > 60 || v.includes('\n')
                    return (
                      <td
                        key={col}
                        className="px-2 py-1.5 text-slate-300 truncate align-top"
                        style={colStyle(col)}
                        title={long ? 'Click to view full value' : v}
                        onClick={() => { if (v) setExpandedCell({ col, value: v }) }}
                      >
                        <span className={long ? 'cursor-pointer underline decoration-dotted decoration-slate-600 hover:decoration-teal-400' : ''}>
                          {v}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Floating bottom scrollbar — pinned to viewport bottom when the
          table's native scrollbar would otherwise be off-screen. */}
      <div
        ref={floatingScrollRef}
        onScroll={onFloatingScroll}
        className="overflow-x-auto bg-slate-900/90 border-t border-slate-700 shadow-lg"
        style={{
          position: 'fixed',
          left: floating.left,
          width: floating.width,
          bottom: 0,
          height: 14,
          zIndex: 30,
          display: floating.visible ? 'block' : 'none',
        }}
      >
        <div style={{ width: innerWidth, height: 1 }} />
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

      {expandedCell && (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
          onClick={() => setExpandedCell(null)}
        >
          <div
            className="bg-slate-900 border border-slate-700 rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-700">
              <span className="text-sm text-slate-300 font-medium">{expandedCell.col.replace(/_/g, ' ')}</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { navigator.clipboard?.writeText(expandedCell.value).catch(() => {}) }}
                  className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
                >
                  Copy
                </button>
                <button
                  onClick={() => setExpandedCell(null)}
                  className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
                >
                  Close
                </button>
              </div>
            </div>
            <pre className="px-4 py-3 text-xs text-slate-200 whitespace-pre-wrap break-words overflow-auto">
              {expandedCell.value}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Individual projection list item ─────────────────────────────────────────

function ProjectionRow({
  proj,
  paperLookup,
  onDeleted,
  selected,
  onToggleSelected,
}: {
  proj: Projection
  paperLookup: Record<string, string>
  onDeleted: (id: string) => void
  selected: boolean
  onToggleSelected: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [deleting, setDeleting] = useState(false)

  return (
    <div className={`border rounded-lg overflow-hidden ${selected ? 'bg-teal-900/20 border-teal-700/50' : 'bg-slate-800 border-slate-700'}`}>
      <div className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-slate-700/50">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelected(proj.projection_id)}
          onClick={e => e.stopPropagation()}
          className="accent-teal-500"
          title="Select for batch actions"
        />
        <button
          onClick={() => setExpanded(s => !s)}
          className="flex-1 flex items-center gap-3 text-left"
        >
        <StatusBadge status={proj.status} />
        <span className="flex-1 text-sm text-slate-300 truncate">
          {paperLookup[proj.project_id] ?? proj.project_id.slice(0, 12)}
        </span>
        {proj.source_type && (
          <span
            className={
              'px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-medium ' +
              (proj.source_type === 'markdown'
                ? 'bg-amber-900/40 text-amber-300 border border-amber-700/40'
                : 'bg-sky-900/40 text-sky-300 border border-sky-700/40')
            }
            title={
              proj.source_type === 'markdown'
                ? 'Projected directly from processed Markdown (no frame extraction)'
                : 'Projected from curated knowledge frame'
            }
          >
            {proj.source_type === 'markdown' ? 'md' : 'frame'}
          </span>
        )}
        <span className="text-xs text-slate-500">
          {proj.times_reviewed > 0 ? `Reviewed ${proj.times_reviewed}×` : 'Raw'}
          {' · '}v{proj.space_version}
          {proj.extracted_at ? ` · ${proj.extracted_at.slice(0, 10)}` : ''}
        </span>
        <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
        </button>
      </div>

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
          <div className="pt-2 flex justify-end">
            <button
              onClick={async () => {
                if (!confirm('Delete this projection? This cannot be undone.')) return
                setDeleting(true)
                try {
                  await deleteProjection(proj.projection_id)
                  onDeleted(proj.projection_id)
                } catch {
                  alert('Failed to delete projection.')
                  setDeleting(false)
                }
              }}
              disabled={deleting}
              className="px-3 py-1 text-xs rounded bg-red-900/40 hover:bg-red-800/60 text-red-300 border border-red-700/40 disabled:opacity-40"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </div>
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
  const [selectedProjectionIds, setSelectedProjectionIds] = useState<Set<string>>(new Set())
  const [batchDeleting, setBatchDeleting] = useState(false)

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

  // ── Review mode + selection-derived project_ids ───────────────────────
  const [reviewMode, setReviewMode] = useState<'per_project' | 'session'>('per_project')

  // Map selected projection_ids → distinct project_ids that own them
  const selectedProjectIds = useMemo(() => {
    if (selectedProjectionIds.size === 0) return [] as string[]
    const pids = new Set<string>()
    for (const proj of projections) {
      if (selectedProjectionIds.has(proj.projection_id) && proj.project_id) {
        pids.add(proj.project_id)
      }
    }
    return Array.from(pids)
  }, [selectedProjectionIds, projections])

  const startReview = async () => {
    try {
      setIsReviewing(true)
      const { reviewProjections } = await import('../api/projections')
      const params: {
        space_id: string
        project_ids?: string[]
        mode: 'per_project' | 'session'
      } = { space_id: selectedSpaceId, mode: reviewMode }
      if (selectedProjectIds.length > 0) {
        params.project_ids = selectedProjectIds
      }
      const { job_id } = await reviewProjections(params)
      pollJob(job_id)
    } catch { setIsReviewing(false) }
  }

  const pollJob = (jobId: string) => {
    let consecutiveErrors = 0
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        consecutiveErrors = 0
        setReviewJob(job)
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          setTimeout(poll, 1000)
        } else {
          setIsReviewing(false)
          if (job.status === 'COMPLETED') loadProjections()
        }
      } catch (error) {
        consecutiveErrors += 1
        if (shouldStopJobPolling(error, consecutiveErrors)) {
          setIsReviewing(false)
          return
        }
        setTimeout(poll, nextJobPollDelayMs(consecutiveErrors))
      }
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
            onClick={startReview}
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
          <a
            href={selectedSpaceId ? `/api/spaces/${selectedSpaceId}/export?format=yaml` : '#'}
            onClick={e => { if (!selectedSpaceId) e.preventDefault() }}
            className="text-xs text-amber-400 hover:text-amber-300"
            title="Download all projections for this space as a ZIP of YAML files"
          >⬇ YAML</a>
          <a
            href={selectedSpaceId ? `/api/spaces/${selectedSpaceId}/export?format=json` : '#'}
            onClick={e => { if (!selectedSpaceId) e.preventDefault() }}
            className="text-xs text-amber-400 hover:text-amber-300"
            title="Download all projections for this space as a ZIP of JSON files"
          >⬇ JSON</a>
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
                    />
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Individual projection list ── */}
          <div>
            <div className="flex items-center gap-2 flex-wrap mb-3 pt-2 border-t border-slate-700">
              <h3 className="text-base font-semibold text-slate-200">
                Individual Projections
              </h3>
              <button
                onClick={selectAllProjections}
                className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
              >
                Select all ({projections.length})
              </button>
              {selectedProjectionIds.size > 0 && (
                <>
                  <span className="text-[11px] text-slate-400">
                    {selectedProjectionIds.size} selected
                  </span>
                  <button
                    onClick={clearProjectionSelection}
                    className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
                  >
                    Clear
                  </button>
                  <button
                    onClick={() => batchDeleteIds(Array.from(selectedProjectionIds))}
                    disabled={batchDeleting}
                    className="text-[11px] px-2 py-0.5 rounded bg-red-900/50 hover:bg-red-800/70 text-red-200 border border-red-700/40 disabled:opacity-40"
                  >
                    {batchDeleting ? 'Deleting…' : `Delete ${selectedProjectionIds.size} selected`}
                  </button>
                </>
              )}
            </div>
            <div className="space-y-1">
              {projections.map(p => (
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
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
