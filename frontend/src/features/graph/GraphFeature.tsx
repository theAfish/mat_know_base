import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'
import { getKnowledgeGraph, getReviewCounts, reviewGraph, clearGraph } from '../../api/graph'
import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import JobProgress from '../../components/JobProgress'
import type { GraphConcept, GraphRelation, GraphPayload, Job } from '../../types'
import { isJobActive, useJobsStore } from '../../store/jobsStore'
import { buildVisOptions, coverageColor, degreeGlow, EDGE_COLORS, glowNode, lerpColor, seedPosition, type NodeColor } from './visualModel'

interface VisGraphProps {
  concepts: GraphConcept[]
  relations: GraphRelation[]
  reviewCounts: Record<string, number> | null
  nodeColorMode: string
  edgeColorMode: string
}

type GraphSelection =
  | { kind: 'node'; concept: GraphConcept }
  | { kind: 'edge'; relation: GraphRelation }

interface HoverCardState {
  item: GraphSelection
  x: number
  y: number
}

function VisGraph({ concepts, relations, reviewCounts, nodeColorMode, edgeColorMode }: VisGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const nodesRef = useRef<DataSet<{ id: string; label: string; title: string; color: NodeColor; size: number; x: number; y: number }> | null>(null)
  const edgesRef = useRef<DataSet<{ id: string; from: string; to: string; title: string; color: { color: string; opacity: number; highlight: string; hover: string } }> | null>(null)
  const [hoverCard, setHoverCard] = useState<HoverCardState | null>(null)
  const [selectedItem, setSelectedItem] = useState<GraphSelection | null>(null)
  const [stabilizing, setStabilizing] = useState(false)
  const [stabilizeProgress, setStabilizeProgress] = useState(0)

  // ── Pre-compute shared quantities ─────────────────────────────────────────
  const degree = useMemo<Record<string, number>>(() => {
    const d: Record<string, number> = {}
    relations.forEach(r => {
      d[r.source] = (d[r.source] ?? 0) + 1
      d[r.target] = (d[r.target] ?? 0) + 1
    })
    return d
  }, [relations])
  const maxDegree = useMemo(() => Math.max(...Object.values(degree), 1), [degree])
  const rc = useMemo(() => reviewCounts ?? {}, [reviewCounts])
  const maxRc = useMemo(() => Math.max(...Object.values(rc), 0), [rc])
  const conceptByLabel = useMemo(() => Object.fromEntries(concepts.map(concept => [concept.label, concept])), [concepts])
  const relationById = useMemo(
    () => Object.fromEntries(relations.map((relation, index) => [`e${index}`, relation])),
    [relations],
  )

  const EV_LABELS: Record<number, string> = { 1: 'causal', 2: 'direct', 3: 'correlative', 4: 'predicted' }

  const buildNode = useCallback((c: GraphConcept, index: number, total: number) => {
    const deg      = degree[c.label] ?? 0
    const aliasN   = (c.aliases ?? []).length
    const t        = Math.min(deg / maxDegree, 1)
    // Default: degree → hue gradient; alias count adds a small size bonus.
    let color: NodeColor = degreeGlow(t)
    let size = 12 + Math.round(t * 18) + Math.min(aliasN, 5) * 1.2
    const lines = [c.label]
    const aliases = (c.aliases ?? []).join(', ')
    if (aliases) lines.push(`Aliases: ${aliases}`)
    lines.push(`Connections: ${deg}`)
    if (nodeColorMode === 'review_coverage') {
      const n = rc[c.label] ?? 0
      const accent = coverageColor(n, maxRc)
      color = glowNode(lerpColor('#0a0f18', accent, 0.15), accent)
      lines.push(n > 0 ? `Reviewed: ${n}×` : 'Never reviewed')
    } else if (nodeColorMode === 'modification_heat') {
      const n = c.modification_count ?? 0
      const accent = coverageColor(n, maxRc)
      color = glowNode(lerpColor('#0a0f18', accent, 0.15), accent)
      lines.push(`Modified: ${n}×`)
    } else if (nodeColorMode === 'connectivity') {
      // Explicit connectivity mode: same gradient but larger size range.
      color = degreeGlow(t)
      size = 12 + Math.round(t * 26)
    }
    const { x, y } = seedPosition(index, total)
    return { id: c.label, label: '', title: lines.join('\n'), color, size, x, y }
  }, [nodeColorMode, rc, maxRc, degree, maxDegree])

  const buildEdge = useCallback((r: GraphRelation, i: number) => {
    const ev = typeof r.evidence_level === 'number' ? r.evidence_level : 3
    let color = '#475569' // slate-600 — much more visible on dark bg than #555
    if (edgeColorMode === 'evidence_level') {
      color = EDGE_COLORS[ev] ?? '#94a3b8'
    } else if (edgeColorMode === 'review_coverage' || edgeColorMode === 'modification_heat') {
      const key = `${r.source}→${r.target}`
      color = coverageColor(rc[key] ?? 0, maxRc)
    }
    return {
      id: `e${i}`,
      from: r.source,
      to: r.target,
      title: `${r.relation}\nEvidence: ${EV_LABELS[ev] ?? ev}`,
      color: { color, opacity: 0.55, highlight: color, hover: color },
    }
  }, [edgeColorMode, rc, maxRc])

  // ── Effect 1: create the network when graph structure changes ─────────────
  useEffect(() => {
    if (!containerRef.current) return

    const total = concepts.length
    const visNodes = new DataSet(concepts.map((c, i) => buildNode(c, i, total)))
    const visEdges = new DataSet(relations.map(buildEdge))
    nodesRef.current = visNodes
    edgesRef.current = visEdges

    networkRef.current?.destroy()
    networkRef.current = new Network(
      containerRef.current,
      { nodes: visNodes, edges: visEdges },
      buildVisOptions(concepts.length, relations.length),
    )

    const network = networkRef.current

    // Show a progress overlay while the force solver settles, then freeze the
    // layout — gives us a clustered look without paying physics cost forever.
    setStabilizing(concepts.length > 0)
    setStabilizeProgress(0)
    const handleProgress = (params: { iterations: number; total: number }) => {
      if (params.total > 0) setStabilizeProgress(params.iterations / params.total)
    }
    const handleStabilized = () => {
      setStabilizing(false)
      setStabilizeProgress(1)
      network.setOptions({ physics: { enabled: false } })
      network.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } })
    }
    network.on('stabilizationProgress', handleProgress)
    network.on('stabilizationIterationsDone', handleStabilized)

    const getHoverPosition = (x: number, y: number) => {
      if (!containerRef.current) return { x, y }
      const rect = containerRef.current.getBoundingClientRect()
      return {
        x: Math.min(x + 16, Math.max(rect.width - 280, 16)),
        y: Math.min(y + 16, Math.max(rect.height - 170, 16)),
      }
    }

    const handleHoverNode = (params: { node?: string; pointer: { DOM: { x: number; y: number } } }) => {
      if (!params.node) return
      const concept = conceptByLabel[params.node]
      if (!concept) return
      const position = getHoverPosition(params.pointer.DOM.x, params.pointer.DOM.y)
      setHoverCard({ item: { kind: 'node', concept }, ...position })
    }

    const handleHoverEdge = (params: { edge?: string; pointer: { DOM: { x: number; y: number } } }) => {
      if (!params.edge) return
      const relation = relationById[params.edge]
      if (!relation) return
      const position = getHoverPosition(params.pointer.DOM.x, params.pointer.DOM.y)
      setHoverCard({ item: { kind: 'edge', relation }, ...position })
    }

    const clearHover = () => setHoverCard(current => (current ? null : current))

    const handleClick = (params: { nodes: string[]; edges: string[] }) => {
      const [nodeId] = params.nodes
      if (nodeId && conceptByLabel[nodeId]) {
        setSelectedItem({ kind: 'node', concept: conceptByLabel[nodeId] })
        return
      }
      const [edgeId] = params.edges
      if (edgeId && relationById[edgeId]) {
        setSelectedItem({ kind: 'edge', relation: relationById[edgeId] })
        return
      }
      setSelectedItem(null)
    }

    // ── Local-physics-on-drag ────────────────────────────────────────────────
    // Physics is off in the steady state for performance. When the user grabs
    // a node, briefly turn physics back on so neighbors react in real time;
    // turn it off again shortly after the drag ends. The dragged node itself
    // is pinned by vis-network for the duration, so forces only propagate
    // through springs to its connected neighborhood — local in effect even
    // though the solver is global.
    let dragSettleTimer: ReturnType<typeof setTimeout> | null = null
    const handleDragStart = (params: { nodes: string[] }) => {
      if (params.nodes.length === 0) return // panning the canvas — leave physics off
      if (dragSettleTimer) { clearTimeout(dragSettleTimer); dragSettleTimer = null }
      network.setOptions({ physics: { enabled: true } })
    }
    const handleDragEnd = (params: { nodes: string[] }) => {
      if (params.nodes.length === 0) return
      // Let the neighborhood settle for a beat, then freeze again.
      if (dragSettleTimer) clearTimeout(dragSettleTimer)
      dragSettleTimer = setTimeout(() => {
        network.setOptions({ physics: { enabled: false } })
        dragSettleTimer = null
      }, 600)
    }

    network.on('hoverNode', handleHoverNode)
    network.on('hoverEdge', handleHoverEdge)
    network.on('blurNode', clearHover)
    network.on('blurEdge', clearHover)
    network.on('click', handleClick)
    network.on('dragStart', handleDragStart)
    network.on('dragEnd', handleDragEnd)

    return () => {
      network.off('hoverNode', handleHoverNode)
      network.off('hoverEdge', handleHoverEdge)
      network.off('blurNode', clearHover)
      network.off('blurEdge', clearHover)
      network.off('click', handleClick)
      network.off('dragStart', handleDragStart)
      network.off('dragEnd', handleDragEnd)
      network.off('stabilizationProgress', handleProgress)
      network.off('stabilizationIterationsDone', handleStabilized)
      if (dragSettleTimer) clearTimeout(dragSettleTimer)
      networkRef.current?.destroy()
      networkRef.current = null
      nodesRef.current = null
      edgesRef.current = null
    }
  }, [concepts, relations, conceptByLabel, relationById]) // intentionally exclude color modes — handled by effect 2

  // ── Effect 2: update colors in-place without destroying the network ───────
  useEffect(() => {
    if (!nodesRef.current || !edgesRef.current) return
    const total = concepts.length
    // Only push color/size updates here — omit x/y so we don't yank nodes back
    // to their seed positions on every color-mode change.
    nodesRef.current.update(concepts.map((c, i) => {
      const n = buildNode(c, i, total)
      return { id: n.id, label: n.label, title: n.title, color: n.color, size: n.size }
    }))
    edgesRef.current.update(relations.map(buildEdge))
  }, [nodeColorMode, edgeColorMode, reviewCounts, buildNode, buildEdge, concepts, relations])

  useEffect(() => {
    setHoverCard(current => {
      if (!current) return current
      if (current.item.kind === 'node' && conceptByLabel[current.item.concept.label]) return current
      if (current.item.kind === 'edge' && Object.values(relationById).includes(current.item.relation)) return current
      return null
    })
    setSelectedItem(current => {
      if (!current) return current
      if (current.kind === 'node') return conceptByLabel[current.concept.label] ? current : null
      return relations.includes(current.relation) ? current : null
    })
  }, [conceptByLabel, relationById, relations])

  // ── Effect 3: redraw on container resize ──────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => {
      if (networkRef.current) {
        networkRef.current.setSize('100%', '100%')
        networkRef.current.redraw()
      }
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const renderHoverSummary = () => {
    if (!hoverCard) return null
    const { item, x, y } = hoverCard
    const content = item.kind === 'node'
      ? [
          { label: 'Concept', value: item.concept.label },
          { label: 'Aliases', value: (item.concept.aliases ?? []).join(', ') || 'None' },
          { label: 'Connections', value: String(degree[item.concept.label] ?? 0) },
        ]
      : [
          { label: 'Relation', value: item.relation.relation },
          { label: 'Path', value: `${item.relation.source} -> ${item.relation.target}` },
          { label: 'Evidence', value: EV_LABELS[item.relation.evidence_level ?? 3] ?? String(item.relation.evidence_level ?? 3) },
        ]

    return (
      <div
        className="pointer-events-none absolute z-20 w-64 rounded-lg border border-slate-600 bg-slate-950/95 px-3 py-2 shadow-2xl"
        style={{ left: x, top: y }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-300">
          {item.kind === 'node' ? 'Node' : 'Edge'}
        </p>
        <div className="mt-2 space-y-1">
          {content.map(entry => (
            <div key={entry.label} className="text-xs leading-5 text-slate-200">
              <span className="text-slate-400">{entry.label}:</span> {entry.value}
            </div>
          ))}
        </div>
      </div>
    )
  }

  const renderDetails = () => {
    if (!selectedItem) return null
    if (selectedItem.kind === 'node') {
      const concept = selectedItem.concept
      return (
        <div className="absolute bottom-4 right-4 z-20 flex w-[24rem] flex-col rounded-xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl backdrop-blur" style={{ maxHeight: 'calc(100% - 2rem)' }}>
          <div className="flex shrink-0 items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-300">Node details</p>
              <h3 className="mt-1 text-lg font-semibold text-slate-100">{concept.label}</h3>
            </div>
            <button
              type="button"
              onClick={() => setSelectedItem(null)}
              className="text-sm text-slate-400 hover:text-slate-200"
            >
              Close
            </button>
          </div>
          <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto text-sm text-slate-200">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Aliases</p>
              <p>{(concept.aliases ?? []).join(', ') || 'None'}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Connections</p>
                <p>{degree[concept.label] ?? 0}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Modified</p>
                <p>{concept.modification_count ?? 0}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Projects</p>
                <p>{concept.source_project_ids?.length ?? 0}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Frames</p>
                <p>{concept.source_frame_ids?.length ?? 0}</p>
              </div>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Source project IDs</p>
              <p className="break-words">{concept.source_project_ids?.join(', ') || 'None'}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Source frame IDs</p>
              <p className="break-words">{concept.source_frame_ids?.join(', ') || 'None'}</p>
            </div>
          </div>
        </div>
      )
    }

    const relation = selectedItem.relation
    return (
      <div className="absolute bottom-4 right-4 z-20 flex w-[24rem] flex-col rounded-xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl backdrop-blur" style={{ maxHeight: 'calc(100% - 2rem)' }}>
        <div className="flex shrink-0 items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-300">Edge details</p>
            <h3 className="mt-1 text-lg font-semibold text-slate-100">{relation.relation}</h3>
          </div>
          <button
            type="button"
            onClick={() => setSelectedItem(null)}
            className="text-sm text-slate-400 hover:text-slate-200"
          >
            Close
          </button>
        </div>
        <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto text-sm text-slate-200">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-400">Direction</p>
            <p>{`${relation.source} -> ${relation.target}`}</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Evidence</p>
              <p>{EV_LABELS[relation.evidence_level ?? 3] ?? String(relation.evidence_level ?? 3)}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Modified</p>
              <p>{relation.modification_count ?? 0}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Project</p>
              <p>{relation.source_project_id ?? 'Unknown'}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Frame</p>
              <p>{relation.source_frame_id ?? 'Unknown'}</p>
            </div>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-400">Knowledge reference</p>
            <pre className="mt-1 max-h-32 overflow-auto rounded bg-slate-950/70 p-2 text-xs text-slate-300 whitespace-pre-wrap">
              {relation.knowledge_ref ? JSON.stringify(relation.knowledge_ref, null, 2) : 'None'}
            </pre>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        minHeight: '400px',
        // Subtle radial vignette gives the graph some depth without distracting
        // from the nodes. Pure black bg made dragged-with-hidden-edges visible.
        background:
          'radial-gradient(ellipse at center, #1a2030 0%, #0e1117 55%, #07090d 100%)',
      }}
    >
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%' }}
      />
      {stabilizing && (
        <div className="pointer-events-none absolute left-1/2 top-4 z-30 -translate-x-1/2 rounded-full border border-slate-700 bg-slate-900/90 px-4 py-2 text-xs text-slate-200 shadow-lg backdrop-blur">
          <div className="flex items-center gap-3">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-teal-400 border-t-transparent" />
            <span>Computing layout… {Math.round(stabilizeProgress * 100)}%</span>
            <div className="h-1 w-32 overflow-hidden rounded-full bg-slate-700">
              <div
                className="h-full bg-teal-400 transition-[width] duration-100"
                style={{ width: `${Math.round(stabilizeProgress * 100)}%` }}
              />
            </div>
          </div>
        </div>
      )}
      {renderHoverSummary()}
      {renderDetails()}
    </div>
  )
}

// ─── Color mode controls ──────────────────────────────────────────────────────

const NODE_MODES = [
  { value: 'default',           label: 'Default (teal)' },
  { value: 'review_coverage',   label: 'Review coverage' },
  { value: 'modification_heat', label: 'Modification heat' },
  { value: 'connectivity',      label: 'Connectivity' },
]

const EDGE_MODES = [
  { value: 'evidence_level',    label: 'Evidence level' },
  { value: 'review_coverage',   label: 'Review coverage' },
  { value: 'modification_heat', label: 'Modification heat' },
  { value: 'default',           label: 'Default (gray)' },
]

// ─── Review panel ─────────────────────────────────────────────────────────────

const REVIEW_MODES = [
  { value: 'auto',   label: 'Auto' },
  { value: 'global', label: 'Global' },
  { value: 'local',  label: 'Local' },
]

const ACTION_ICONS: Record<string, string> = {
  merge: '🔀', standardize: '🏷️', delete: '🗑️',
}
const TOOL_ICONS: Record<string, string> = {
  get_concept_details: '🔍',
  get_concept_neighbors: '🌐',
  get_relation_type_distribution: '📊',
  search_graph_elements: '🔎',
  merge_concepts: '🔀',
  standardize_relation_name: '🏷️',
  delete_concept: '🗑️',
  delete_relation: '🗑️',
}

interface ReviewPanelProps {
  onReviewComplete: () => void
}

function ReviewPanel({ onReviewComplete }: ReviewPanelProps) {
  const [mode, setMode] = useState('auto')
  const [seedCount, setSeedCount] = useState(10)
  const [reviewJobId, setReviewJobId] = useState<string | null>(null)
  const reviewJob = useJobsStore(state => reviewJobId ? state.jobs[reviewJobId] ?? null : null)
  const isRunning = !!reviewJobId && (!reviewJob || isJobActive(reviewJob))
  const [expanded, setExpanded] = useState(false)

  const startReview = async () => {
    try {
      setExpanded(true)
      const { job_id } = await reviewGraph({ mode, seed_count: seedCount })
      setReviewJobId(job_id)
    } catch {
      setReviewJobId(null)
    }
  }

  useEffect(() => {
    const finished = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (job.job_id === reviewJobId && job.status === 'COMPLETED') onReviewComplete()
    }
    window.addEventListener(JOB_FINISHED_EVENT, finished)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, finished)
  }, [onReviewComplete, reviewJobId])

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(s => !s)}
        className="w-full px-4 py-3 text-left text-sm font-medium text-slate-200 flex items-center justify-between hover:bg-slate-700/50"
      >
        <span>Run Graph Review</span>
        <span className="text-slate-400">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-slate-700">
          <div className="grid grid-cols-3 gap-3 mt-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Mode</label>
              <select
                value={mode}
                onChange={e => setMode(e.target.value)}
                disabled={isRunning}
                className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-teal-500 disabled:opacity-50"
              >
                {REVIEW_MODES.map(m => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Seed concepts</label>
              <input
                type="number"
                min={1}
                max={50}
                value={seedCount}
                onChange={e => setSeedCount(Number(e.target.value))}
                disabled={isRunning}
                className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-teal-500 disabled:opacity-50"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={startReview}
                disabled={isRunning}
                className="w-full px-3 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm font-medium"
              >
                {isRunning ? 'Running…' : 'Start Review'}
              </button>
            </div>
          </div>

          {reviewJob && (
            <div className="space-y-1">
              {isRunning && (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin" />
                  Review in progress…
                </div>
              )}
              {/* Event log */}
              {reviewJob.events && reviewJob.events.length > 0 && (
                <div className="space-y-0.5">
                  {reviewJob.events.slice(-10).reverse().map((ev, i) => {
                    const action = (ev as Record<string, string>).action ?? ''
                    const tool = (ev as Record<string, string>).tool ?? ''
                    const label = (ev as Record<string, string>).label ?? ''
                    const etype = (ev as Record<string, string>).element_type ?? ''
                    const icon = ACTION_ICONS[action] ?? TOOL_ICONS[tool] ?? '·'
                    return (
                      <p key={i} className="text-xs text-slate-500">
                        {icon} <code className="text-slate-400">{tool || action}</code>
                        {etype && ` — ${etype}`}{label && `: ${label}`}
                      </p>
                    )
                  })}
                </div>
              )}
              {/* Result */}
              {reviewJob.status === 'COMPLETED' && reviewJob.result && (
                <div className="bg-green-900/30 border border-green-700/50 rounded px-3 py-2 text-xs text-green-200">
                  {(() => {
                    const r = reviewJob.result as Record<string, unknown>
                    const stats = (r.reviewed_elements ?? {}) as Record<string, number>
                    return `Review complete — mode: ${r.mode} · examined: ${stats.examined ?? 0} · modified: ${stats.modified ?? 0}`
                  })()}
                </div>
              )}
              {reviewJob.status === 'FAILED' && (
                <p className="text-xs text-red-400">Review failed: {reviewJob.error}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function GraphPage() {
  const [payload, setPayload] = useState<GraphPayload | null>(null)
  const [reviewCounts, setReviewCounts] = useState<Record<string, number> | null>(null)
  const [loading, setLoading] = useState(true)
  const [nodeColorMode, setNodeColorMode] = useState('default')
  const [edgeColorMode, setEdgeColorMode] = useState('evidence_level')
  const [showVizOptions, setShowVizOptions] = useState(false)

  const REVIEW_MODES_SET = new Set(['review_coverage', 'modification_heat'])

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true)
    try {
      const data = await getKnowledgeGraph()
      setPayload(data)
    } catch { setPayload(null) }
    setLoading(false)
  }, [])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (job.status === 'COMPLETED' && ['knowledge_graph', 'graph_review'].includes(job.kind)) {
        load()
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load])

  // Load review counts when a review-based mode is selected
  useEffect(() => {
    if (REVIEW_MODES_SET.has(nodeColorMode) || REVIEW_MODES_SET.has(edgeColorMode)) {
      getReviewCounts()
        .then(data => setReviewCounts(data as Record<string, number>))
        .catch(() => {})
    }
  }, [nodeColorMode, edgeColorMode])

  const graph = payload?.graph
  const concepts = graph?.concepts ?? []
  const relations = graph?.relations ?? []

  if (loading) {
    return (
      <div className="p-6">
        <h2 className="text-xl font-semibold mb-4">Dataset Graph</h2>
        <p className="text-slate-400 text-sm">Loading…</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-700 flex-shrink-0 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Dataset Graph</h2>
            <p className="text-sm text-slate-400">Merged knowledge graph across all datasets.</p>
          </div>
          <div className="flex gap-3 text-center">
            <div className="bg-slate-800 rounded px-3 py-1.5">
              <div className="text-lg font-bold text-teal-400">{concepts.length}</div>
              <div className="text-xs text-slate-400">Concepts</div>
            </div>
            <div className="bg-slate-800 rounded px-3 py-1.5">
              <div className="text-lg font-bold text-teal-400">{relations.length}</div>
              <div className="text-xs text-slate-400">Relations</div>
            </div>
            <div className="bg-slate-800 rounded px-3 py-1.5">
              <div className="text-lg font-bold text-teal-400">{payload?.projection_count ?? 0}</div>
              <div className="text-xs text-slate-400">Projections</div>
            </div>
          </div>
        </div>

        {/* Viz options */}
        <div>
          <button
            onClick={() => setShowVizOptions(s => !s)}
            className="text-xs text-teal-400 hover:text-teal-300"
          >
            {showVizOptions ? '▲ Hide options' : '▼ Visualization options'}
          </button>
          {showVizOptions && (
            <div className="grid grid-cols-2 gap-3 mt-2">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Node color</label>
                <select
                  value={nodeColorMode}
                  onChange={e => setNodeColorMode(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                >
                  {NODE_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Edge color</label>
                <select
                  value={edgeColorMode}
                  onChange={e => setEdgeColorMode(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                >
                  {EDGE_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Graph canvas or empty state */}
      {concepts.length === 0 ? (
        <div className="flex-1 p-6 space-y-4">
          <p className="text-slate-400 text-sm">No merged graph data yet. Run graph extraction first.</p>
          <ReviewPanel onReviewComplete={load} />
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 relative overflow-hidden">
            <div style={{ position: 'absolute', inset: 0 }}>
              <VisGraph
                concepts={concepts}
                relations={relations}
                reviewCounts={reviewCounts}
                nodeColorMode={nodeColorMode}
                edgeColorMode={edgeColorMode}
              />
            </div>
          </div>
          <div className="px-6 py-3 border-t border-slate-700 flex-shrink-0">
            <ReviewPanel onReviewComplete={load} />
          </div>
        </div>
      )}
    </div>
  )
}
