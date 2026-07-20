import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, UIEvent } from 'react'
import {
  flexRender,
  functionalUpdate,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import type { ColumnDef, ColumnSizingState, PaginationState, SortingFn, SortingState } from '@tanstack/react-table'

import {
  type ColPrefs,
  PAGE_SIZE,
  defaultColumns,
  exportTableRows,
  loadColPrefs,
  saveColPrefs,
  slugifyExportName,
} from './helpers'
import {
  compareText, inferColumnDataType, isBlank, loadSavedPage, parseBoolean, parseDate, parseNumber,
  savePage, sortArrow, sortLabel, type ColumnDataType, type ProjectionTableRow,
} from '../../features/projections/tableModel'

const SELECTION_COLUMN_ID = '__projection_selection__'

export default function SectionTable({
  name,
  rows,
  schemaOrder,
  exportBasename,
  onRequestDeleteProjection,
  onRequestReview,
  selectedProjectionIds,
  onToggleProjection,
  onClearSelection,
  reviewDisabled,
}: {
  name: string
  rows: ProjectionTableRow[]
  schemaOrder?: string[]
  exportBasename?: string
  onRequestDeleteProjection?: (projectionIds: string[]) => void
  onRequestReview?: (projectionIds: string[]) => void
  selectedProjectionIds: Set<string>
  onToggleProjection: (projectionId: string) => void
  onClearSelection: () => void
  reviewDisabled?: boolean
}) {
  const pageStorageKey = exportBasename ?? name
  const [page, setPage] = useState(() => loadSavedPage(pageStorageKey))
  const [sorting, setSorting] = useState<SortingState>([])
  const [exportingFormat, setExportingFormat] = useState<'csv' | 'excel' | null>(null)
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const start = (page - 1) * PAGE_SIZE

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  useEffect(() => {
    setPage(loadSavedPage(pageStorageKey))
  }, [pageStorageKey])

  useEffect(() => {
    savePage(pageStorageKey, Math.min(page, totalPages))
  }, [page, pageStorageKey, totalPages])

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
      const added = allCols.filter(column => !p.known.includes(column))
      if (added.length === 0) return p
      const known = [...p.known, ...added]
      // New fields can be introduced by a post-processor after the user has
      // saved column preferences. Reveal them once without re-enabling any
      // columns the user previously hid.
      const visible = Array.from(new Set([
        ...p.visible,
        ...defaultColumns(added, schemaOrder).filter(column => !p.visible.includes(column)),
      ]))
      const next = { ...p, known, visible }
      saveColPrefs(name, next)
      return next
    })
  }, [allCols, name, schemaOrder])

  const visibleCols = prefs.visible.filter(c => prefs.known.includes(c))

  const updatePrefs = (fn: (p: ColPrefs) => ColPrefs) =>
    setPrefs(p => {
      const next = fn(p)
      saveColPrefs(name, next)
      return next
    })

  const [expandedCell, setExpandedCell] = useState<{ col: string; value: string } | null>(null)

  const rowKey = (row: ProjectionTableRow) => row.projection_id || ''
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

  const columnVisibility = useMemo(() => {
    const state: Record<string, boolean> = { [SELECTION_COLUMN_ID]: true }
    for (const col of prefs.known) state[col] = visibleCols.includes(col)
    return state
  }, [prefs.known, visibleCols])

  const columnOrder = useMemo(
    () => [
      SELECTION_COLUMN_ID,
      ...visibleCols,
      ...prefs.known.filter(col => !visibleCols.includes(col)),
    ],
    [prefs.known, visibleCols],
  )

  const columnDataTypes = useMemo(() => {
    const types: Record<string, ColumnDataType> = {}
    for (const col of prefs.known) types[col] = inferColumnDataType(rows, col)
    return types
  }, [prefs.known, rows])

  const typedSortingFn = useMemo<SortingFn<ProjectionTableRow>>(() => (leftRow, rightRow, columnId) => {
    const left = leftRow.getValue(columnId)
    const right = rightRow.getValue(columnId)
    const leftBlank = isBlank(left)
    const rightBlank = isBlank(right)
    const currentSort = sorting.find(sort => sort.id === columnId)
    if (leftBlank && rightBlank) return 0
    if (leftBlank) return currentSort?.desc ? -1 : 1
    if (rightBlank) return currentSort?.desc ? 1 : -1

    const type = columnDataTypes[columnId] ?? 'text'
    if (type === 'boolean') return (parseBoolean(left) ?? 0) - (parseBoolean(right) ?? 0)
    if (type === 'number') return (parseNumber(left) ?? 0) - (parseNumber(right) ?? 0)
    if (type === 'date') return (parseDate(left) ?? 0) - (parseDate(right) ?? 0)
    return compareText(left, right)
  }, [columnDataTypes, sorting])

  const cycleSort = (columnId: string) => {
    const current = sorting.find(sort => sort.id === columnId)
    setPage(1)
    savePage(pageStorageKey, 1)
    if (!current) setSorting([{ id: columnId, desc: false }])
    else if (!current.desc) setSorting([{ id: columnId, desc: true }])
    else setSorting([])
  }

  const columns = useMemo<ColumnDef<ProjectionTableRow>[]>(() => [
    {
      id: SELECTION_COLUMN_ID,
      size: 32,
      minSize: 32,
      maxSize: 32,
      enableResizing: false,
      enableSorting: false,
      header: () => (
        <input
          type="checkbox"
          checked={allSectionSelected}
          ref={el => { if (el) el.indeterminate = someSectionSelected }}
          onChange={toggleSectionAll}
          className="accent-teal-500"
          title={allSectionSelected ? 'Deselect all rows in this section' : `Select all ${allPageProjectionIds.length} rows in this section (across all pages)`}
        />
      ),
      cell: ({ row }) => {
        const projectionId = rowKey(row.original)
        if (!projectionId) return null
        return (
          <input
            type="checkbox"
            checked={selectedProjectionIds.has(projectionId)}
            onChange={() => onToggleProjection(projectionId)}
            className="accent-teal-500"
          />
        )
      },
    },
    ...prefs.known.map(col => ({
      accessorKey: col,
      id: col,
      size: prefs.widths[col] ?? 320,
      minSize: 40,
      maxSize: 1200,
      header: () => col.replace(/_/g, ' '),
      sortingFn: typedSortingFn,
      cell: ({ getValue }: { getValue: () => unknown }) => {
        const value = String(getValue() ?? '')
        const long = value.length > 60 || value.includes('\n')
        return (
          <span className={long ? 'cursor-pointer underline decoration-dotted decoration-slate-600 hover:decoration-teal-400' : ''}>
            {value}
          </span>
        )
      },
    })),
  ], [allPageProjectionIds.length, allSectionSelected, onToggleProjection, prefs.known, prefs.widths, selectedProjectionIds, someSectionSelected, typedSortingFn])

  const pagination = useMemo<PaginationState>(() => ({ pageIndex: page - 1, pageSize: PAGE_SIZE }), [page])

  const table = useReactTable({
    data: rows,
    columns,
    defaultColumn: {
      minSize: 40,
      maxSize: 1200,
    },
    state: {
      columnOrder,
      columnSizing: prefs.widths,
      columnVisibility,
      pagination,
      sorting,
    },
    columnResizeMode: 'onChange',
    onColumnSizingChange: updater => updatePrefs(p => ({
      ...p,
      widths: functionalUpdate(updater, p.widths) as ColumnSizingState,
    })),
    onPaginationChange: updater => {
      const next = functionalUpdate(updater, pagination)
      setPage(next.pageIndex + 1)
      savePage(pageStorageKey, next.pageIndex + 1)
    },
    onSortingChange: updater => {
      setPage(1)
      savePage(pageStorageKey, 1)
      setSorting(functionalUpdate(updater, sorting))
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  const pageRows = table.getRowModel().rows

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

  const onFloatingScroll = (e: UIEvent<HTMLDivElement>) => {
    if (bodyScrollRef.current && bodyScrollRef.current.scrollLeft !== e.currentTarget.scrollLeft)
      bodyScrollRef.current.scrollLeft = e.currentTarget.scrollLeft
  }
  const onBodyScroll = (e: UIEvent<HTMLDivElement>) => {
    if (floatingScrollRef.current && floatingScrollRef.current.scrollLeft !== e.currentTarget.scrollLeft)
      floatingScrollRef.current.scrollLeft = e.currentTarget.scrollLeft
  }

  const [showPicker, setShowPicker] = useState(false)

  // Drag-to-reorder column state
  const [draggingCol, setDraggingCol] = useState<string | null>(null)
  const [dragOverCol, setDragOverCol] = useState<string | null>(null)
  const dragColRef = useRef<string | null>(null)

  const toggleColVisible = (col: string) => updatePrefs(p => {
    if (p.visible.includes(col)) {
      setSorting(current => current.filter(sort => sort.id !== col))
      return { ...p, visible: p.visible.filter(c => c !== col) }
    }
    return { ...p, visible: [...p.visible, col] }
  })
  const resetPrefs = () => updatePrefs(() => ({
    visible: defaultColumns(allCols, schemaOrder),
    widths: {},
    known: allCols,
  }))

  const handleExport = useCallback(async (format: 'csv' | 'excel') => {
    if (visibleCols.length === 0) {
      alert('Show at least one column before exporting this table.')
      return
    }
    setExportingFormat(format)
    try {
      const sortedRowsForExport = table.getSortedRowModel().rows.map(row => row.original)
      exportTableRows(
        sortedRowsForExport.map(row => Object.fromEntries(visibleCols.map(col => [col, row[col] ?? '']))),
        visibleCols,
        exportBasename ?? slugifyExportName(name),
        format,
      )
    } catch (error) {
      alert(`Export failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setExportingFormat(null)
    }
  }, [exportBasename, name, table, visibleCols])

  const colStyle = (col: string): CSSProperties => {
    const column = table.getColumn(col)
    if (!column) return { maxWidth: '20rem' }
    const width = column.getSize()
    return { width, minWidth: width, maxWidth: width }
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
        <button
          onClick={() => handleExport('csv')}
          disabled={exportingFormat !== null || visibleCols.length === 0}
          className="text-[11px] px-2 py-0.5 rounded bg-amber-900/20 hover:bg-amber-800/30 disabled:opacity-40 text-amber-300 border border-amber-700/40"
          title="Export this table as CSV using only the currently visible columns"
        >
          {exportingFormat === 'csv' ? 'Exporting…' : '⬇ CSV'}
        </button>
        <button
          onClick={() => handleExport('excel')}
          disabled={exportingFormat !== null || visibleCols.length === 0}
          className="text-[11px] px-2 py-0.5 rounded bg-amber-900/20 hover:bg-amber-800/30 disabled:opacity-40 text-amber-300 border border-amber-700/40"
          title="Export this table as an Excel-compatible file using only the currently visible columns"
        >
          {exportingFormat === 'excel' ? 'Exporting…' : '⬇ Excel'}
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
            {table.getHeaderGroups().map(headerGroup => (
              <tr key={headerGroup.id} className="border-b border-slate-700">
              {headerGroup.headers.map(header => {
                const col = header.column.id
                if (col === SELECTION_COLUMN_ID) {
                  return (
                    <th key={header.id} className="px-2 py-1.5 w-8" style={colStyle(col)}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  )
                }
                return (
                <th
                  key={header.id}
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
                  <div className="flex items-center gap-1.5 pr-2">
                    <span className="truncate">{flexRender(header.column.columnDef.header, header.getContext())}</span>
                    <button
                      type="button"
                      onClick={e => {
                        e.preventDefault()
                        e.stopPropagation()
                        cycleSort(col)
                      }}
                      onMouseDown={e => e.stopPropagation()}
                      className={`shrink-0 px-0.5 text-[13px] leading-none transition-colors ${
                        header.column.getIsSorted()
                          ? 'text-teal-300'
                          : 'text-slate-600 hover:text-slate-300'
                      }`}
                      title={sortLabel(columnDataTypes[col] ?? 'text', header.column.getIsSorted())}
                    >
                      {sortArrow(header.column.getIsSorted())}
                    </button>
                  </div>
                  <div
                    onMouseDown={e => {
                      e.preventDefault()
                      e.stopPropagation()
                      header.getResizeHandler()(e)
                    }}
                    onTouchStart={e => {
                      e.stopPropagation()
                      header.getResizeHandler()(e)
                    }}
                    style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 5, cursor: 'col-resize' }}
                  />
                </th>
              )})}
            </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-800">
            {pageRows.map(row => {
              const k = rowKey(row.original)
              const selectable = !!k
              const selected = selectable && selectedProjectionIds.has(k)
              return (
                <tr key={row.id} className={selected ? 'bg-teal-900/20' : 'hover:bg-slate-800/40'}>
                  {row.getVisibleCells().map(cell => {
                    const col = cell.column.id
                    if (col === SELECTION_COLUMN_ID) {
                      return (
                        <td key={cell.id} className="px-2 py-1.5 w-8 align-top" style={colStyle(col)}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      )
                    }
                    const value = String(cell.getValue() ?? '')
                    const long = value.length > 60 || value.includes('\n')
                    return (
                      <td
                        key={cell.id}
                        className="px-2 py-1.5 text-slate-300 truncate align-top"
                        style={colStyle(col)}
                        title={long ? 'Click to view full value' : value}
                        onClick={() => { if (value) setExpandedCell({ col, value }) }}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
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
            className="hover:text-slate-200 disabled:opacity-30 transition-colors select-none">‹</button>
          {(() => {
            let pages: (number | '...')[]
            if (totalPages <= 7) {
              pages = Array.from({ length: totalPages }, (_, i) => i + 1)
            } else if (page <= 4) {
              pages = [1, 2, 3, 4, 5, '...', totalPages]
            } else if (page >= totalPages - 3) {
              pages = [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
            } else {
              pages = [1, '...', page - 1, page, page + 1, '...', totalPages]
            }
            return pages.map((p, idx) =>
              p === '...'
                ? <span key={`ellipsis-${idx}`} className="select-none">…</span>
                : <button key={p} onClick={() => setPage(p as number)}
                    className={`transition-colors ${p === page
                      ? 'text-blue-400 font-bold'
                      : 'hover:text-slate-200'}`}>
                    {p}
                  </button>
            )
          })()}
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            className="hover:text-slate-200 disabled:opacity-30 transition-colors select-none">›</button>
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
