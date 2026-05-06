import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { Network, type Options } from 'vis-network'
import { DataSet } from 'vis-data'
import { getKnowledgeGraph, getReviewCounts, reviewGraph, clearGraph } from '../api/graph'
import { getJob } from '../api/jobs'
import JobProgress from '../components/JobProgress'
import type { GraphConcept, GraphRelation, GraphPayload, Job } from '../types'

// ─── Color helpers (matching original graph_viz.py) ──────────────────────────

const EDGE_COLORS: Record<number, string> = {
  1: '#22c55e', // causal
  2: '#3b82f6', // direct observation
  3: '#eab308', // correlative
  4: '#f97316', // predicted
}

function lerpColor(c1: string, c2: string, t: number): string {
  const h = (s: string) => [parseInt(s.slice(1,3),16), parseInt(s.slice(3,5),16), parseInt(s.slice(5,7),16)] as [number,number,number]
  const [r1,g1,b1] = h(c1), [r2,g2,b2] = h(c2)
  const r = Math.round(r1+(r2-r1)*t), g = Math.round(g1+(g2-g1)*t), b = Math.round(b1+(b2-b1)*t)
  return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`
}

function coverageColor(count: number, maxCount: number): string {
  if (maxCount <= 0 || count <= 0) return '#6b7280'
  const t = Math.min(count / maxCount, 1)
  return t < 0.5 ? lerpColor('#6b7280', '#34d399', t / 0.5) : lerpColor('#34d399', '#f59e0b', (t - 0.5) / 0.5)
}

// ─── vis-network graph component ─────────────────────────────────────────────

const VIS_OPTIONS: Options = {
  layout: { improvedLayout: false },
  physics: {
    solver: 'barnesHut',
    barnesHut: {
      gravitationalConstant: -6000,
      centralGravity: 0.3,
      springLength: 110,
      springConstant: 0.05,
      damping: 0.12,
      avoidOverlap: 0.1,
    },
    stabilization: { enabled: true, iterations: 80, updateInterval: 25 },
  },
  nodes: {
    font: { size: 0 },   // hidden by default, shown as tooltip on hover
    borderWidth: 1,
    shadow: false,
    shape: 'dot',
  },
  edges: {
    font: { size: 0 },
    smooth: false,
    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
  },
  interaction: {
    hover: true,
    tooltipDelay: 80,
    hideEdgesOnDrag: true,
    hideNodesOnDrag: false,
  },
}

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
  const nodesRef = useRef<DataSet<{ id: string; label: string; title: string; color: string; size: number }> | null>(null)
  const edgesRef = useRef<DataSet<{ id: string; from: string; to: string; title: string; color: { color: string; opacity: number } }> | null>(null)
  const [hoverCard, setHoverCard] = useState<HoverCardState | null>(null)
  const [selectedItem, setSelectedItem] = useState<GraphSelection | null>(null)

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

  const buildNode = useCallback((c: GraphConcept) => {
    let color = '#34d399', size = 16
    const lines = [c.label]
    const aliases = (c.aliases ?? []).join(', ')
    if (aliases) lines.push(`Aliases: ${aliases}`)
    if (nodeColorMode === 'review_coverage') {
      const n = rc[c.label] ?? 0
      color = coverageColor(n, maxRc)
      lines.push(n > 0 ? `Reviewed: ${n}×` : 'Never reviewed')
    } else if (nodeColorMode === 'modification_heat') {
      const n = c.modification_count ?? 0
      color = coverageColor(n, maxRc)
      lines.push(`Modified: ${n}×`)
    } else if (nodeColorMode === 'connectivity') {
      const deg = degree[c.label] ?? 0
      const t = Math.min(deg / maxDegree, 1)
      color = lerpColor('#60a5fa', '#f97316', t)
      size = 12 + Math.round(t * 18)
      lines.push(`Connections: ${deg}`)
    }
    return { id: c.label, label: '', title: lines.join('\n'), color, size }
  }, [nodeColorMode, rc, maxRc, degree, maxDegree])

  const buildEdge = useCallback((r: GraphRelation, i: number) => {
    const ev = typeof r.evidence_level === 'number' ? r.evidence_level : 3
    let color = '#555'
    if (edgeColorMode === 'evidence_level') {
      color = EDGE_COLORS[ev] ?? '#888'
    } else if (edgeColorMode === 'review_coverage' || edgeColorMode === 'modification_heat') {
      const key = `${r.source}→${r.target}`
      color = coverageColor(rc[key] ?? 0, maxRc)
    }
    return { id: `e${i}`, from: r.source, to: r.target, title: `${r.relation}\nEvidence: ${EV_LABELS[ev] ?? ev}`, color: { color, opacity: 0.8 } }
  }, [edgeColorMode, rc, maxRc])

  // ── Effect 1: create the network when graph structure changes ─────────────
  useEffect(() => {
    if (!containerRef.current) return

    const enablePhysics = concepts.length <= 500 && relations.length <= 1200

    const visNodes = new DataSet(concepts.map(buildNode))
    const visEdges = new DataSet(relations.map(buildEdge))
    nodesRef.current = visNodes
    edgesRef.current = visEdges

    networkRef.current?.destroy()
    networkRef.current = new Network(
      containerRef.current,
      { nodes: visNodes, edges: visEdges },
      {
        ...VIS_OPTIONS,
        physics: {
          ...VIS_OPTIONS.physics,
          enabled: enablePhysics,
          stabilization: { enabled: enablePhysics, iterations: 80, updateInterval: 25 },
        },
      },
    )

    const network = networkRef.current

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

    network.on('hoverNode', handleHoverNode)
    network.on('hoverEdge', handleHoverEdge)
    network.on('blurNode', clearHover)
    network.on('blurEdge', clearHover)
    network.on('click', handleClick)

    return () => {
      network.off('hoverNode', handleHoverNode)
      network.off('hoverEdge', handleHoverEdge)
      network.off('blurNode', clearHover)
      network.off('blurEdge', clearHover)
      network.off('click', handleClick)
      networkRef.current?.destroy()
      networkRef.current = null
      nodesRef.current = null
      edgesRef.current = null
    }
  }, [concepts, relations, conceptByLabel, relationById]) // intentionally exclude color modes — handled by effect 2

  // ── Effect 2: update colors in-place without destroying the network ───────
  useEffect(() => {
    if (!nodesRef.current || !edgesRef.current) return
    nodesRef.current.update(concepts.map(buildNode))
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
        <div className="absolute bottom-4 right-4 z-20 w-[24rem] rounded-xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl backdrop-blur">
          <div className="flex items-start justify-between gap-3">
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
          <div className="mt-4 space-y-3 text-sm text-slate-200">
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
      <div className="absolute bottom-4 right-4 z-20 w-[24rem] rounded-xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl backdrop-blur">
        <div className="flex items-start justify-between gap-3">
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
        <div className="mt-4 space-y-3 text-sm text-slate-200">
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
    <div className="relative h-full w-full" style={{ minHeight: '400px', background: '#0e1117' }}>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%' }}
      />
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
  const [reviewJob, setReviewJob] = useState<Job | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const startReview = async () => {
    try {
      setIsRunning(true)
      setExpanded(true)
      const { job_id } = await reviewGraph({ mode, seed_count: seedCount })
      pollJob(job_id)
    } catch {
      setIsRunning(false)
    }
  }

  const pollJob = (jobId: string) => {
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        setReviewJob(job)
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          setTimeout(poll, 1000)
        } else {
          setIsRunning(false)
          if (job.status === 'COMPLETED') onReviewComplete()
        }
      } catch { setTimeout(poll, 2000) }
    }
    setTimeout(poll, 500)
  }

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

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getKnowledgeGraph()
      setPayload(data)
    } catch { setPayload(null) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

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
