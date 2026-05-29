import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  type ColPrefs,
  PAGE_SIZE,
  defaultColumns,
  loadColPrefs,
  saveColPrefs,
} from './helpers'


export default function SectionTable({
  name,
  rows,
  schemaOrder,
  onRequestDeleteProjection,
  onRequestReview,
  selectedProjectionIds,
  onToggleProjection,
  onClearSelection,
  reviewDisabled,
}: {
  name: string
  rows: Array<Record<string, string>>
  schemaOrder?: string[]
  onRequestDeleteProjection?: (projectionIds: string[]) => void
  onRequestReview?: (projectionIds: string[]) => void
  selectedProjectionIds: Set<string>
  onToggleProjection: (projectionId: string) => void
  onClearSelection: () => void
  reviewDisabled?: boolean
}) {
  const [page, setPage] = useState(1)
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const start = (page - 1) * PAGE_SIZE
  const pageRows = rows.slice(start, start + PAGE_SIZE)

  const allCols = useMemo(() => {
    const seen = new Set<string>()
    for (const r of rows) for (const k of Object.keys(r)) seen.add(k)
    return Array.from(seen)
  }, [rows])

  const [prefs, setPrefs] = useState<ColPrefs>(() => {
    const saved = loadColPrefs(name)
    const defaults = defaultColumns(allCols.length > 0 ? allCols : [], schemaOrder)
    if (!saved) return { visible: defaults, widths: {}, known: allCols }
    const merged = { ...saved, known: Array.from(new Set([...saved.known, ...allCols])) }
    return merged
  })

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

  const updatePrefs = (fn: (p: ColPrefs) => ColPrefs) =>
    setPrefs(p => {
      const next = fn(p)
      saveColPrefs(name, next)
      return next
    })

  const [expandedCell, setExpandedCell] = useState<{ col: string; value: string } | null>(null)

  const rowKey = (row: Record<string, string>) => row.projection_id || ''
  const pageProjectionIds = Array.from(new Set(pageRows.map(rowKey).filter(Boolean)))
  const allPageProjectionIds = Array.from(new Set(rows.map(rowKey).filter(Boolean)))
  const allSectionSelected =
    allPageProjectionIds.length > 0 &&
    allPageProjectionIds.every(id => selectedProjectionIds.has(id))
  const someSectionSelected =
    !allSectionSelected &&
    allPageProjectionIds.some(id => selectedProjectionIds.has(id))
  // If any are selected (partial or all) → deselect all; if none → select all.
  const toggleSectionAll = () => {
    if (allSectionSelected || someSectionSelected) {
      allPageProjectionIds.filter(id => selectedProjectionIds.has(id)).forEach(id => onToggleProjection(id))
    } else {
      allPageProjectionIds.forEach(id => onToggleProjection(id))
    }
  }
  const sectionSelectedProjectionIds = useMemo(
    () => allPageProjectionIds.filter(id => selectedProjectionIds.has(id)),
    [allPageProjectionIds, selectedProjectionIds],
  )

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

  const [showPicker, setShowPicker] = useState(false)

  // Drag-to-reorder column state
  const [draggingCol, setDraggingCol] = useState<string | null>(null)
  const [dragOverCol, setDragOverCol] = useState<string | null>(null)
  const dragColRef = useRef<string | null>(null)

  // Drag-to-resize column state
  const resizingRef = useRef<{ col: string; startX: number; startWidth: number } | null>(null)
  const prefsRef = useRef(prefs)
  prefsRef.current = prefs

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      const r = resizingRef.current
      if (!r) return
      const delta = e.clientX - r.startX
      const newWidth = Math.max(40, Math.min(1200, Math.round(r.startWidth + delta)))
      setPrefs(p => ({ ...p, widths: { ...p.widths, [r.col]: newWidth } }))
    }
    const onMouseUp = () => {
      if (!resizingRef.current) return
      resizingRef.current = null
      saveColPrefs(name, prefsRef.current)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [name])

  const toggleColVisible = (col: string) => updatePrefs(p => {
    if (p.visible.includes(col)) return { ...p, visible: p.visible.filter(c => c !== col) }
    return { ...p, visible: [...p.visible, col] }
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
        {sectionSelectedProjectionIds.length > 0 && (
          <>
            <span className="text-[11px] text-slate-400">
              {sectionSelectedProjectionIds.length} projection(s) selected in this section
            </span>
            <button
              onClick={onClearSelection}
              className="text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
            >
              Clear
            </button>
            {onRequestReview && (
              <button
                onClick={() => onRequestReview(sectionSelectedProjectionIds)}
                disabled={reviewDisabled}
                className="text-[11px] px-2 py-0.5 rounded bg-teal-700 hover:bg-teal-600 disabled:opacity-40 text-white"
                title="Run reviewer on selected projections"
              >
                ▶ Review {sectionSelectedProjectionIds.length}
              </button>
            )}
            {onRequestDeleteProjection && (
              <button
                onClick={() => onRequestDeleteProjection(sectionSelectedProjectionIds)}
                className="text-[11px] px-2 py-0.5 rounded bg-red-900/50 hover:bg-red-800/70 text-red-200 border border-red-700/40"
                title="Delete every selected projection that contributed any row in this section"
              >
                Delete {sectionSelectedProjectionIds.length}
              </button>
            )}
          </>
        )}
      </div>

      {showPicker && (
        <div className="bg-slate-900 border border-slate-700 rounded p-3 text-xs space-y-2 max-h-72 overflow-y-auto">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">Column visibility</span>
            <button onClick={resetPrefs} className="text-teal-400 hover:text-teal-300">Reset defaults</button>
          </div>
          <div className="grid grid-cols-1 gap-1">
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
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div ref={bodyScrollRef} onScroll={onBodyScroll} className="overflow-x-auto">
        <table className="text-xs border-collapse" style={{ minWidth: '100%' }}>
          <thead>
            <tr className="border-b border-slate-700">
              <th className="px-2 py-1.5 w-8">
                <input
                  type="checkbox"
                  checked={allSectionSelected}
                  ref={el => { if (el) el.indeterminate = someSectionSelected }}
                  onChange={toggleSectionAll}
                  className="accent-teal-500"
                  title={allSectionSelected ? 'Deselect all rows in this section' : `Select all ${allPageProjectionIds.length} rows in this section (across all pages)`}
                />
              </th>
              {visibleCols.map(col => (
                <th
                  key={col}
                  draggable
                  onDragStart={() => { dragColRef.current = col; setDraggingCol(col) }}
                  onDragOver={e => { e.preventDefault(); if (dragOverCol !== col) setDragOverCol(col) }}
                  onDrop={() => {
                    const from = dragColRef.current
                    if (from && from !== col) {
                      updatePrefs(p => {
                        const vis = [...p.visible]
                        const fromIdx = vis.indexOf(from)
                        const toIdx = vis.indexOf(col)
                        if (fromIdx < 0 || toIdx < 0) return p
                        vis.splice(fromIdx, 1)
                        vis.splice(toIdx, 0, from)
                        return { ...p, visible: vis }
                      })
                    }
                    setDraggingCol(null); setDragOverCol(null); dragColRef.current = null
                  }}
                  onDragEnd={() => { setDraggingCol(null); setDragOverCol(null); dragColRef.current = null }}
                  className={`text-left px-2 py-1.5 text-slate-400 font-medium whitespace-nowrap select-none cursor-grab${draggingCol === col ? ' opacity-40' : ''}${dragOverCol === col && draggingCol !== col ? ' border-l-2 border-teal-400' : ''}`}
                  style={{ ...colStyle(col), position: 'relative' }}
                >
                  {col.replace(/_/g, ' ')}
                  <div
                    onMouseDown={e => {
                      e.preventDefault()
                      e.stopPropagation()
                      const th = e.currentTarget.parentElement as HTMLElement
                      resizingRef.current = { col, startX: e.clientX, startWidth: th.getBoundingClientRect().width }
                    }}
                    style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 5, cursor: 'col-resize' }}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {pageRows.map((row, i) => {
              const k = rowKey(row)
              const selectable = !!k
              const selected = selectable && selectedProjectionIds.has(k)
              return (
                <tr key={start + i} className={selected ? 'bg-teal-900/20' : 'hover:bg-slate-800/40'}>
                  <td className="px-2 py-1.5 w-8 align-top">
                    {selectable && (
                      <input type="checkbox" checked={selected} onChange={() => onToggleProjection(k)} className="accent-teal-500" />
                    )}
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
            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded">‹</button>
          <span>Page {page} / {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded">›</button>
          <span className="ml-1 text-slate-500">
            (rows {start + 1}–{Math.min(rows.length, start + PAGE_SIZE)} of {rows.length})
          </span>
        </div>
      )}

      {expandedCell && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={() => setExpandedCell(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-700">
              <span className="text-sm text-slate-300 font-medium">{expandedCell.col.replace(/_/g, ' ')}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => { navigator.clipboard?.writeText(expandedCell.value).catch(() => {}) }}
                  className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200">Copy</button>
                <button onClick={() => setExpandedCell(null)}
                  className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200">Close</button>
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
