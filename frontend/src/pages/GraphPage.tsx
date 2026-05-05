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

function VisGraph({ concepts, relations, reviewCounts, nodeColorMode, edgeColorMode }: VisGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const nodesRef = useRef<DataSet<{ id: string; label: string; title: string; color: string; size: number }> | null>(null)
  const edgesRef = useRef<DataSet<{ id: string; from: string; to: string; title: string; color: { color: string; opacity: number } }> | null>(null)

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

    return () => {
      networkRef.current?.destroy()
      networkRef.current = null
      nodesRef.current = null
      edgesRef.current = null
    }
  }, [concepts, relations]) // intentionally exclude color modes — handled by effect 2

  // ── Effect 2: update colors in-place without destroying the network ───────
  useEffect(() => {
    if (!nodesRef.current || !edgesRef.current) return
    nodesRef.current.update(concepts.map(buildNode))
    edgesRef.current.update(relations.map(buildEdge))
  }, [nodeColorMode, edgeColorMode, reviewCounts, buildNode, buildEdge, concepts, relations])

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

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', minHeight: '400px', background: '#0e1117' }}
    />
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
