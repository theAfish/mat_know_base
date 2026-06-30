import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import {
  assignProjectsToGroup, createProjectGroup,
  deleteProjectGroup, listProjectGroups, updateProjectGroup,
} from '../api/projectGroups'
import StatusBadge from './StatusBadge'
import BatchActionBar from './BatchActionBar'
import type { Project, ProjectGroup, Space } from '../types'
import { projectDisplayName } from '../utils/projectName'

// ─── Auto-scroll during drag ────────────────────────────────────────────────
// When the user drags near the top or bottom edge of the viewport, scroll the
// page automatically so they don't have to drop-release-scroll-pick-up again.

function useDragAutoScroll({
  edgePx = 120,  // px from scroll-container edge that activates scrolling
  maxSpeed = 18, // max px scrolled per animation frame (~1080 px/s)
} = {}) {
  const posRef      = useRef<number | null>(null) // latest drag clientY
  const scrollElRef = useRef<HTMLElement | null>(null)
  const rafRef      = useRef<number | null>(null)
  const dragging    = useRef(false)

  useEffect(() => {
    /** Walk up the DOM to find the first scrollable ancestor. */
    function findScrollParent(el: HTMLElement | null): HTMLElement {
      if (!el || el === document.documentElement) return document.documentElement
      const { overflowY } = window.getComputedStyle(el)
      if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight)
        return el
      return findScrollParent(el.parentElement as HTMLElement)
    }

    const tick = () => {
      const y  = posRef.current
      const el = scrollElRef.current
      if (y !== null && el) {
        const rect = el.getBoundingClientRect()
        const relY = y - rect.top
        const h    = rect.height
        let speed  = 0
        if (relY < edgePx)        speed = -maxSpeed * (1 - relY / edgePx)
        else if (relY > h - edgePx) speed =  maxSpeed * ((relY - (h - edgePx)) / edgePx)
        if (speed !== 0) el.scrollTop += speed
      }
      rafRef.current = requestAnimationFrame(tick)
    }

    const onDragStart = (e: DragEvent) => {
      dragging.current = true
      scrollElRef.current = findScrollParent(e.target as HTMLElement)
      rafRef.current = requestAnimationFrame(tick)
    }

    const onDragOver = (e: DragEvent) => { posRef.current = e.clientY }

    const stop = () => {
      dragging.current = false
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
      posRef.current = null
    }

    // Capture-phase wheel listener — fires even during HTML5 drag in Chrome/Edge.
    // Lets the user scroll with the mouse wheel while holding a drag.
    const onWheel = (e: WheelEvent) => {
      if (!dragging.current || !scrollElRef.current) return
      scrollElRef.current.scrollTop += e.deltaY
      e.preventDefault()
    }

    document.addEventListener('dragstart', onDragStart, true)
    document.addEventListener('dragover',  onDragOver)
    document.addEventListener('dragend',   stop)
    document.addEventListener('drop',      stop)
    document.addEventListener('wheel',     onWheel, { passive: false, capture: true })
    return () => {
      document.removeEventListener('dragstart', onDragStart, true)
      document.removeEventListener('dragover',  onDragOver)
      document.removeEventListener('dragend',   stop)
      document.removeEventListener('drop',      stop)
      document.removeEventListener('wheel',     onWheel, { capture: true })
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [edgePx, maxSpeed])
}

// ─── Shared types ───────────────────────────────────────────────────────────

export interface ProjectColumn {
  /** Column header label. */
  header: string
  /** Optional extra header classes. */
  headerClassName?: string
  /** Cell renderer. */
  render: (p: Project) => ReactNode
  /** Optional extra cell classes (applied per row). */
  cellClassName?: string
}

interface GroupSection {
  key: string
  group: ProjectGroup | null     // null for the ungrouped pseudo-group
  projects: Project[]
}

const UNGROUPED_KEY = '__ungrouped__'
const DND_MIME = 'application/x-mkb-project-ids'

// ─── Component ──────────────────────────────────────────────────────────────

interface Props {
  projects: Project[]
  /** Called when the component performs an optimistic update on the project list. */
  onProjectsChange: (projects: Project[]) => void
  /** Reload from server (used after delete/move failures and by BatchActionBar). */
  onRefresh: () => void

  spaces: Space[]
  /** Frame-status accessor used by filter pills & BatchActionBar. */
  getStatus: (id: string) => string

  /** Extra columns rendered after the project Label cell. */
  columns: ProjectColumn[]
  /** Optional trailing action cell (e.g. Manage / View button). */
  rowAction?: (p: Project) => ReactNode
  /** Label for the trailing action column (empty string for no header). */
  rowActionHeader?: string

  /** Optional message when there are no projects at all. */
  emptyMessage?: string
}

export default function ProjectGroupedList({
  projects,
  onProjectsChange,
  onRefresh,
  spaces,
  getStatus,
  columns,
  rowAction,
  rowActionHeader = '',
  emptyMessage = 'No projects yet.',
}: Props) {
  useDragAutoScroll()

  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [groupsLoaded, setGroupsLoaded] = useState(false)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [filterProcessing, setFilterProcessing] = useState<string | null>(null)
  const [filterFrame, setFilterFrame] = useState<string | null>(null)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null)
  const [editingGroupName, setEditingGroupName] = useState<string>('')
  const [dropTargetKey, setDropTargetKey] = useState<string | null>(null)
  const [newGroupOpen, setNewGroupOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [groupErr, setGroupErr] = useState<string | null>(null)
  const lastClickedIdx = useRef<number | null>(null)

  // ── Load groups once on mount ──────────────────────────────
  const loadGroups = useCallback(async () => {
    try {
      const gs = await listProjectGroups()
      setGroups(gs)
    } catch (e: unknown) {
      const err = e as { message?: string }
      setGroupErr(err?.message ?? String(e))
    } finally {
      setGroupsLoaded(true)
    }
  }, [])
  useEffect(() => { loadGroups() }, [loadGroups])

  // ── Filtering ──────────────────────────────────────────────
  const visibleProjects = useMemo(() =>
    projects.filter(p => {
      if (filterProcessing && (p.processing_status ?? 'UNPROCESSED') !== filterProcessing) return false
      if (filterFrame && (p.frame_status ?? 'NO_FRAME') !== filterFrame) return false
      return true
    }),
    [projects, filterProcessing, filterFrame],
  )

  // ── Bucket into ordered sections ───────────────────────────
  const sections = useMemo<GroupSection[]>(() => {
    const buckets = new Map<string, Project[]>()
    buckets.set(UNGROUPED_KEY, [])
    for (const g of groups) buckets.set(g.group_id, [])
    for (const p of visibleProjects) {
      const key = p.group_id ?? UNGROUPED_KEY
      if (!buckets.has(key)) {
        // Orphan group reference → ungrouped
        buckets.get(UNGROUPED_KEY)!.push(p)
      } else {
        buckets.get(key)!.push(p)
      }
    }
    const ordered: GroupSection[] = [
      { key: UNGROUPED_KEY, group: null, projects: buckets.get(UNGROUPED_KEY)! },
    ]
    for (const g of groups) {
      ordered.push({ key: g.group_id, group: g, projects: buckets.get(g.group_id) ?? [] })
    }
    // Hide ungrouped section when empty AND at least one group exists.
    if (ordered[0].projects.length === 0 && groups.length > 0) ordered.shift()
    return ordered
  }, [visibleProjects, groups])

  const flatVisible = useMemo(() => {
    const out: Project[] = []
    for (const s of sections) {
      if (collapsedGroups.has(s.key)) continue
      out.push(...s.projects)
    }
    return out
  }, [sections, collapsedGroups])

  const flatIdxByProject = useMemo(() => {
    const m = new Map<string, number>()
    flatVisible.forEach((p, i) => m.set(p.project_id, i))
    return m
  }, [flatVisible])

  // ── Filter pill counts ─────────────────────────────────────
  const processingOptions = useMemo(() => {
    const counts: Record<string, number> = {}
    projects.forEach(p => { const s = p.processing_status ?? 'UNPROCESSED'; counts[s] = (counts[s] ?? 0) + 1 })
    return Object.entries(counts).sort()
  }, [projects])

  const frameOptions = useMemo(() => {
    const counts: Record<string, number> = {}
    projects.forEach(p => { const s = p.frame_status ?? 'NO_FRAME'; counts[s] = (counts[s] ?? 0) + 1 })
    return Object.entries(counts).sort()
  }, [projects])

  // ── Selection ──────────────────────────────────────────────
  const handleCheck = (idx: number, id: string, shift: boolean) => {
    if (shift && lastClickedIdx.current !== null) {
      const lo = Math.min(lastClickedIdx.current, idx)
      const hi = Math.max(lastClickedIdx.current, idx)
      setCheckedIds(prev => {
        const next = new Set(prev)
        const adding = !prev.has(id)
        for (let i = lo; i <= hi; i++) {
          if (i >= 0 && i < flatVisible.length) {
            if (adding) next.add(flatVisible[i].project_id)
            else next.delete(flatVisible[i].project_id)
          }
        }
        return next
      })
    } else {
      setCheckedIds(prev => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id); else next.add(id)
        return next
      })
      lastClickedIdx.current = idx
    }
  }

  const toggleSelectGroup = (section: GroupSection) => {
    const ids = section.projects.map(p => p.project_id)
    setCheckedIds(prev => {
      const next = new Set(prev)
      const allIn = ids.length > 0 && ids.every(id => next.has(id))
      if (allIn) ids.forEach(id => next.delete(id))
      else ids.forEach(id => next.add(id))
      return next
    })
  }

  const toggleCollapse = (key: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  // ── Group CRUD ─────────────────────────────────────────────
  const submitNewGroup = async () => {
    const name = newGroupName.trim()
    if (!name) return
    setGroupErr(null)
    try {
      const g = await createProjectGroup({ name })
      setGroups(gs => [...gs, g])
      setNewGroupName('')
      setNewGroupOpen(false)
    } catch (e: unknown) {
      const err = e as { message?: string }
      setGroupErr(`Failed to create group: ${err?.message ?? e}`)
    }
  }

  const handleDeleteGroup = async (g: ProjectGroup) => {
    const inGroup = projects.filter(p => p.group_id === g.group_id).length
    const msg = inGroup > 0
      ? `Delete group "${g.name}"? ${inGroup} project(s) will be ungrouped (not deleted).`
      : `Delete group "${g.name}"?`
    if (!window.confirm(msg)) return
    try {
      await deleteProjectGroup(g.group_id)
      setGroups(gs => gs.filter(x => x.group_id !== g.group_id))
      onProjectsChange(
        projects.map(p => p.group_id === g.group_id ? { ...p, group_id: null } : p),
      )
    } catch (e: unknown) {
      const err = e as { message?: string }
      setGroupErr(`Failed to delete group: ${err?.message ?? e}`)
    }
  }

  const startRenameGroup = (g: ProjectGroup) => {
    setEditingGroupId(g.group_id)
    setEditingGroupName(g.name)
  }

  const commitRenameGroup = async () => {
    if (!editingGroupId) return
    const name = editingGroupName.trim()
    const original = groups.find(g => g.group_id === editingGroupId)
    setEditingGroupId(null)
    if (!original || !name || name === original.name) return
    try {
      const updated = await updateProjectGroup(editingGroupId, { name })
      setGroups(gs => gs.map(g => g.group_id === editingGroupId ? updated : g))
    } catch (e: unknown) {
      const err = e as { message?: string }
      setGroupErr(`Failed to rename group: ${err?.message ?? e}`)
    }
  }

  // ── Assignment (optimistic) ────────────────────────────────
  const assignToGroup = async (projectIds: string[], groupId: string | null) => {
    if (projectIds.length === 0) return
    const idsSet = new Set(projectIds)
    onProjectsChange(
      projects.map(p => idsSet.has(p.project_id) ? { ...p, group_id: groupId } : p),
    )
    try {
      await assignProjectsToGroup(projectIds, groupId)
    } catch (e: unknown) {
      const err = e as { message?: string }
      setGroupErr(`Failed to move project(s): ${err?.message ?? e}`)
      onRefresh()
    }
  }

  // ── Drag and drop ──────────────────────────────────────────
  const onProjectDragStart = (e: React.DragEvent, projectId: string) => {
    const ids = checkedIds.has(projectId) && checkedIds.size > 1
      ? Array.from(checkedIds)
      : [projectId]
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData(DND_MIME, JSON.stringify(ids))
    e.dataTransfer.setData('text/plain', ids.join(','))
  }

  const onGroupDragOver = (e: React.DragEvent, key: string) => {
    if (!e.dataTransfer.types.includes(DND_MIME)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dropTargetKey !== key) setDropTargetKey(key)
  }

  const onGroupDragLeave = (key: string) => {
    if (dropTargetKey === key) setDropTargetKey(null)
  }

  const onGroupDrop = (e: React.DragEvent, sectionKey: string) => {
    if (!e.dataTransfer.types.includes(DND_MIME)) return
    e.preventDefault()
    setDropTargetKey(null)
    let ids: string[] = []
    try { ids = JSON.parse(e.dataTransfer.getData(DND_MIME)) } catch { return }
    if (!Array.isArray(ids) || ids.length === 0) return
    const targetGroupId = sectionKey === UNGROUPED_KEY ? null : sectionKey
    const moving = ids.filter(id => {
      const p = projects.find(x => x.project_id === id)
      return p && (p.group_id ?? null) !== targetGroupId
    })
    if (moving.length === 0) return
    assignToGroup(moving, targetGroupId)
  }

  // ── Render ─────────────────────────────────────────────────
  if (projects.length === 0) {
    return <p className="text-slate-400 text-sm">{emptyMessage}</p>
  }

  // colSpan = select + label + custom columns + trailing action
  const totalCols = 2 + columns.length + (rowAction ? 1 : 0)

  return (
    <div className="space-y-4">
      {checkedIds.size > 0 && (
        <div className="sticky top-0 z-20 -mt-2 pt-2 bg-slate-900/95 backdrop-blur supports-[backdrop-filter]:bg-slate-900/80 shadow-lg shadow-slate-950/40">
          <BatchActionBar
            selectedIds={checkedIds}
            allProjects={projects}
            spaces={spaces}
            getStatus={getStatus}
            onSelectionChange={ids => setCheckedIds(ids)}
            onRefresh={onRefresh}
          />
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        {newGroupOpen ? (
          <div className="flex items-center gap-1">
            <input
              autoFocus
              value={newGroupName}
              onChange={e => setNewGroupName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') submitNewGroup()
                else if (e.key === 'Escape') { setNewGroupOpen(false); setNewGroupName('') }
              }}
              placeholder="Group name"
              className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-xs w-44"
            />
            <button
              onClick={submitNewGroup}
              disabled={!newGroupName.trim()}
              className="px-2 py-1 rounded bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white text-xs font-medium"
            >Add</button>
            <button
              onClick={() => { setNewGroupOpen(false); setNewGroupName('') }}
              className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs"
            >Cancel</button>
          </div>
        ) : (
          <button
            onClick={() => setNewGroupOpen(true)}
            className="px-2.5 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-medium"
          >
            + New group
          </button>
        )}
        {checkedIds.size > 0 && groups.length > 0 && (
          <div className="flex items-center gap-1 text-xs">
            <span className="text-slate-500">Move selected to:</span>
            <select
              value=""
              onChange={e => {
                const v = e.target.value
                if (!v) return
                const targetId = v === UNGROUPED_KEY ? null : v
                assignToGroup(Array.from(checkedIds), targetId)
                e.currentTarget.value = ''
              }}
              className="bg-slate-800 border border-slate-600 rounded px-1.5 py-0.5 text-slate-200"
            >
              <option value="">— choose group —</option>
              <option value={UNGROUPED_KEY}>(Ungrouped)</option>
              {groups.map(g => (
                <option key={g.group_id} value={g.group_id}>{g.name}</option>
              ))}
            </select>
          </div>
        )}
        <span className="text-slate-500 text-xs ml-2">
          {groupsLoaded
            ? 'Tip: drag a project row onto a group header to move it.'
            : 'Loading groups…'}
        </span>
      </div>

      {groupErr && (
        <div className="px-3 py-1.5 rounded bg-red-900/40 border border-red-700 text-red-200 text-xs">
          {groupErr}
          <button onClick={() => setGroupErr(null)} className="ml-2 text-red-300 hover:text-red-100">✕</button>
        </div>
      )}

      {/* Status filter pills */}
      <div className="flex items-center gap-x-3 gap-y-1.5 flex-wrap text-xs">
        <span className="text-slate-500 font-medium">Processed:</span>
        {processingOptions.map(([s, count]) => (
          <button
            key={s}
            onClick={() => setFilterProcessing(v => v === s ? null : s)}
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded border transition-colors ${
              filterProcessing === s ? 'border-teal-500 bg-teal-900/30' : 'border-slate-600 hover:border-slate-500'
            }`}
          >
            <StatusBadge status={s} />
            <span className="text-slate-400">{count}</span>
          </button>
        ))}
        <span className="text-slate-600 mx-1">·</span>
        <span className="text-slate-500 font-medium">Frame:</span>
        {frameOptions.map(([s, count]) => (
          <button
            key={s}
            onClick={() => setFilterFrame(v => v === s ? null : s)}
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded border transition-colors ${
              filterFrame === s ? 'border-teal-500 bg-teal-900/30' : 'border-slate-600 hover:border-slate-500'
            }`}
          >
            <StatusBadge status={s} />
            <span className="text-slate-400">{count}</span>
          </button>
        ))}
        {(filterProcessing || filterFrame) && (
          <button
            onClick={() => { setFilterProcessing(null); setFilterFrame(null) }}
            className="text-slate-500 hover:text-slate-300 ml-1"
          >✕ clear</button>
        )}
        {(filterProcessing || filterFrame) && (
          <button
            onClick={() => setCheckedIds(new Set(visibleProjects.map(p => p.project_id)))}
            className="text-teal-400 hover:text-teal-300 ml-1"
          >select all {visibleProjects.length}</button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400 uppercase tracking-wide border-b border-slate-700">
              <th className="pb-2 pr-2 w-8"></th>
              <th className="pb-2 pr-3 font-medium">Label</th>
              {columns.map((c, i) => (
                <th key={i} className={`pb-2 pr-3 font-medium ${c.headerClassName ?? ''}`}>{c.header}</th>
              ))}
              {rowAction && <th className="pb-2 font-medium">{rowActionHeader}</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {sections.map(section => {
              const collapsed = collapsedGroups.has(section.key)
              const allInSelected = section.projects.length > 0
                && section.projects.every(p => checkedIds.has(p.project_id))
              const someInSelected = !allInSelected
                && section.projects.some(p => checkedIds.has(p.project_id))
              const isDropTarget = dropTargetKey === section.key
              const isEditing = section.group && editingGroupId === section.group.group_id

              return (
                <Fragment key={section.key}>
                  <tr
                    onDragOver={e => onGroupDragOver(e, section.key)}
                    onDragLeave={() => onGroupDragLeave(section.key)}
                    onDrop={e => onGroupDrop(e, section.key)}
                    className={`bg-slate-800/60 ${isDropTarget ? 'outline outline-2 outline-teal-400' : ''}`}
                  >
                    <td colSpan={totalCols} className="py-1.5 px-2">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => toggleCollapse(section.key)}
                          className="text-slate-400 hover:text-slate-200 w-4 text-center"
                          title={collapsed ? 'Expand' : 'Collapse'}
                        >
                          {collapsed ? '▶' : '▼'}
                        </button>
                        <input
                          type="checkbox"
                          checked={allInSelected}
                          ref={el => { if (el) el.indeterminate = someInSelected }}
                          onChange={() => toggleSelectGroup(section)}
                          className="accent-teal-500"
                          disabled={section.projects.length === 0}
                          title={`Select all in ${section.group?.name ?? 'Ungrouped'}`}
                        />
                        {section.group?.color && (
                          <span
                            className="inline-block w-2.5 h-2.5 rounded-full border border-slate-600"
                            style={{ backgroundColor: section.group.color }}
                          />
                        )}
                        {section.group ? (
                          isEditing ? (
                            <input
                              autoFocus
                              value={editingGroupName}
                              onChange={e => setEditingGroupName(e.target.value)}
                              onBlur={commitRenameGroup}
                              onKeyDown={e => {
                                if (e.key === 'Enter') commitRenameGroup()
                                else if (e.key === 'Escape') setEditingGroupId(null)
                              }}
                              className="bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-slate-100 text-sm"
                            />
                          ) : (
                            <button
                              onDoubleClick={() => startRenameGroup(section.group!)}
                              className="text-slate-100 font-medium text-sm hover:text-teal-300"
                              title="Double-click to rename"
                            >
                              {section.group.name}
                            </button>
                          )
                        ) : (
                          <span className="text-slate-300 font-medium text-sm italic">Ungrouped</span>
                        )}
                        <span className="text-slate-500 text-xs">
                          ({section.projects.length})
                        </span>
                        {section.group && !isEditing && (
                          <button
                            onClick={() => handleDeleteGroup(section.group!)}
                            className="ml-auto text-slate-500 hover:text-red-400 text-xs"
                            title="Delete group (does not delete projects)"
                          >
                            ✕
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>

                  {!collapsed && section.projects.map(p => {
                    const idx = flatIdxByProject.get(p.project_id) ?? 0
                    const checked = checkedIds.has(p.project_id)
                    return (
                      <tr
                        key={p.project_id}
                        draggable
                        onDragStart={e => onProjectDragStart(e, p.project_id)}
                        className={checked ? 'bg-teal-900/15' : 'hover:bg-slate-800/50'}
                      >
                        <td className="py-2 pr-2 w-8 pl-6">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={e => handleCheck(idx, p.project_id, (e.nativeEvent as MouseEvent).shiftKey)}
                            className="accent-teal-500"
                          />
                        </td>
                        <td className="py-2 pr-3 text-slate-200 max-w-xs truncate">
                          <span className="text-slate-600 cursor-grab select-none mr-1" title="Drag to move">⋮⋮</span>
                          {projectDisplayName(p)}
                        </td>
                        {columns.map((c, i) => (
                          <td key={i} className={`py-2 pr-3 ${c.cellClassName ?? ''}`}>
                            {c.render(p)}
                          </td>
                        ))}
                        {rowAction && <td className="py-2">{rowAction(p)}</td>}
                      </tr>
                    )
                  })}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
