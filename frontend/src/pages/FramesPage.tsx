import { Fragment, useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { listFrames, getFrame, getFrameHistory } from '../api/frames'
import { listProjects, listAssets, listProcessedAssets, processProject, extractProject, projectToSpace, kgExtractProject } from '../api/projects'
import { listProjections } from '../api/projections'
import { listSpaces } from '../api/spaces'
import { listFeedback, resolveFeedback } from '../api/feedback'
import { getKnowledgeGraph } from '../api/graph'
import { getJob } from '../api/jobs'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'
import StatusBadge from '../components/StatusBadge'
import BatchActionBar from '../components/BatchActionBar'
import type { Frame, Project, Asset, ProcessedAsset, Projection, Space, FeedbackItem, ExtractionPass, Job, GraphConcept, GraphRelation } from '../types'

// ─── Mini graph for per-project graph ────────────────────────────────────────

function MiniGraph({ concepts, relations }: { concepts: GraphConcept[]; relations: GraphRelation[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const nodes = new DataSet(concepts.map(c => ({
      id: c.label, label: '', title: c.label, color: '#0d9488', size: 14,
    })))
    const edges = new DataSet(relations.map((r, i) => ({
      id: `e${i}`, from: r.source, to: r.target, title: r.relation,
      color: { color: '#475569', opacity: 0.8 },
    })))
    const net = new Network(containerRef.current, { nodes, edges }, {
      layout: { improvedLayout: false },
      physics: {
        solver: 'barnesHut',
        barnesHut: { gravitationalConstant: -6000, centralGravity: 0.3, springLength: 110, springConstant: 0.05, damping: 0.12 },
        stabilization: { enabled: true, iterations: 80, updateInterval: 25 },
      },
      nodes: { font: { size: 0 }, borderWidth: 1, shape: 'dot' },
      edges: { font: { size: 0 }, smooth: false, arrows: { to: { enabled: true, scaleFactor: 0.4 } } },
      interaction: { hover: true, tooltipDelay: 80 },
    })
    return () => net.destroy()
  }, [concepts, relations])

  return (
    <div ref={containerRef} style={{ height: 400, background: '#0e1117' }}
      className="rounded-lg overflow-hidden border border-slate-700" />
  )
}

// ─── Tab: Assets ──────────────────────────────────────────────────────────────

function AssetsTab({ projectId }: { projectId: string }) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [processed, setProcessed] = useState<ProcessedAsset[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([listAssets(projectId), listProcessedAssets(projectId)])
      .then(([a, p]) => { setAssets(a); setProcessed(p) })
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (assets.length === 0) return <p className="text-sm text-slate-400">No assets ingested yet.</p>

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium text-slate-400 mb-2">{assets.length} raw asset(s)</p>
        <div className="space-y-0.5">
          {assets.map(a => (
            <div key={a.asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
              <span className="flex-1 truncate text-slate-300">{a.filename}</span>
              <span className="text-slate-500">{a.mime_type ?? '—'}</span>
              <span className="text-slate-500">{a.status ?? '—'}</span>
            </div>
          ))}
        </div>
      </div>
      {processed.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-400 mb-2">{processed.length} processed output(s)</p>
          <div className="space-y-0.5">
            {processed.map(p => (
              <div key={p.processed_asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
                <span className="flex-1 truncate text-slate-300">{p.filename ?? '—'}</span>
                <span className="text-slate-500">{p.processing_type}</span>
                <span className="text-slate-500">.{p.output_format}</span>
                <span className="text-slate-600">{p.artifact_count} artifact(s)</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Tab: Knowledge Frame ─────────────────────────────────────────────────────

// Evidence level → small coloured pill.
const EVIDENCE_META: Record<number, { label: string; cls: string }> = {
  1: { label: 'L1 · causal',      cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50' },
  2: { label: 'L2 · observed',    cls: 'bg-sky-900/50 text-sky-300 border-sky-700/50' },
  3: { label: 'L3 · correlative', cls: 'bg-amber-900/40 text-amber-300 border-amber-700/50' },
  4: { label: 'L4 · inferred',    cls: 'bg-orange-900/40 text-orange-300 border-orange-700/50' },
}

function EvidenceBadge({ level }: { level: number }) {
  const meta = EVIDENCE_META[level]
  if (!meta) return null
  return (
    <span className={`inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border ${meta.cls}`}>
      {meta.label}
    </span>
  )
}

const prettyKey = (k: string) =>
  k.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

// A "section block" is the shape the extraction prompt may emit:
//   { heading, description?, items?, subsections? }
function isSectionBlock(v: unknown): v is {
  heading?: string; description?: string;
  items?: unknown[]; subsections?: Record<string, unknown>;
} {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false
  const o = v as Record<string, unknown>
  return ('heading' in o || 'description' in o) && ('items' in o || 'subsections' in o)
}

// Render any value inline (used inside item-detail rows).
function InlineValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') return <span className="text-slate-600">—</span>
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <span>{String(value)}</span>
  }
  if (Array.isArray(value)) {
    // Array of primitives → comma list. Array of objects → bullet list of objects.
    const allPrim = value.every(v => v === null || typeof v !== 'object')
    if (allPrim) return <span>{value.map(v => String(v)).join(', ')}</span>
    return (
      <ul className="list-disc list-inside space-y-1">
        {value.map((v, i) => (
          <li key={i}><InlineValue value={v} /></li>
        ))}
      </ul>
    )
  }
  // Object → small definition-list
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && v.length === 0))
  return (
    <span className="inline-block">
      {entries.map(([k, v], i) => (
        <span key={k}>
          <span className="text-slate-500">{prettyKey(k)}:</span>{' '}
          <InlineValue value={v} />
          {i < entries.length - 1 ? <span className="text-slate-600">; </span> : null}
        </span>
      ))}
    </span>
  )
}

// Render a single leaf item dict as a wiki-style paragraph card.
function ItemCard({ item }: { item: Record<string, unknown> }) {
  // Pick a headline string from the most "title-like" fields.
  const headlineFields = ['name', 'title', 'claim', 'finding', 'subject', 'property', 'concept']
  const bodyFields = ['description', 'summary', 'detail', 'details', 'explanation', 'statement', 'note', 'notes']
  const evidence = typeof item.evidence_level === 'number' ? item.evidence_level : undefined

  let headline: string | null = null
  for (const f of headlineFields) {
    const v = item[f]
    if (typeof v === 'string' && v.trim()) { headline = v; break }
  }
  let body: string | null = null
  for (const f of bodyFields) {
    const v = item[f]
    if (typeof v === 'string' && v.trim()) { body = v; break }
  }

  const usedKeys = new Set<string>(['evidence_level'])
  if (headline) usedKeys.add(headlineFields.find(f => item[f] === headline)!)
  if (body) usedKeys.add(bodyFields.find(f => item[f] === body)!)

  const rest = Object.entries(item).filter(
    ([k, v]) => !usedKeys.has(k) && v !== null && v !== '' && !(Array.isArray(v) && v.length === 0),
  )

  // Fallback headline: if nothing matched, use the first string field.
  if (!headline) {
    for (const [k, v] of rest) {
      if (typeof v === 'string' && v.trim()) {
        headline = v
        usedKeys.add(k)
        break
      }
    }
  }

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-md px-3 py-2 text-sm">
      {(headline || evidence !== undefined) && (
        <div className="flex items-start gap-2">
          {headline && <span className="font-medium text-slate-100 flex-1">{headline}</span>}
          {evidence !== undefined && <EvidenceBadge level={evidence} />}
        </div>
      )}
      {body && <p className="mt-1 text-slate-300 leading-relaxed">{body}</p>}
      {rest.length > 0 && (
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs text-slate-300">
          {rest
            .filter(([k]) => !usedKeys.has(k))
            .map(([k, v]) => (
              <Fragment key={k}>
                <dt className="text-slate-500">{prettyKey(k)}</dt>
                <dd className="min-w-0"><InlineValue value={v} /></dd>
              </Fragment>
            ))}
        </dl>
      )}
    </div>
  )
}

// Render a list of items as cards (or as a bullet list of primitives).
function ItemList({ items }: { items: unknown[] }) {
  if (items.length === 0) return null
  const allPrim = items.every(it => it === null || typeof it !== 'object')
  if (allPrim) {
    return (
      <ul className="list-disc list-inside text-sm text-slate-300 space-y-0.5">
        {items.map((it, i) => <li key={i}>{String(it)}</li>)}
      </ul>
    )
  }
  return (
    <div className="space-y-2">
      {items.map((it, i) =>
        it && typeof it === 'object' && !Array.isArray(it)
          ? <ItemCard key={i} item={it as Record<string, unknown>} />
          : <div key={i} className="text-sm text-slate-300">{String(it)}</div>,
      )}
    </div>
  )
}

// Recursive wiki section. depth controls heading size and indent.
function FrameSection({
  name, value, depth = 1, defaultOpen = true,
}: { name: string; value: unknown; depth?: number; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  // Resolve heading + payload.
  let heading: string = prettyKey(name)
  let description: string | undefined
  let items: unknown[] | undefined
  let subsections: Record<string, unknown> | undefined
  let primitive: unknown
  let arrayItems: unknown[] | undefined
  let objectFallback: Record<string, unknown> | undefined

  if (isSectionBlock(value)) {
    if (typeof value.heading === 'string' && value.heading.trim()) heading = value.heading
    description = typeof value.description === 'string' ? value.description : undefined
    items = Array.isArray(value.items) ? value.items : undefined
    subsections = value.subsections && typeof value.subsections === 'object'
      ? value.subsections as Record<string, unknown>
      : undefined
  } else if (Array.isArray(value)) {
    arrayItems = value
  } else if (value !== null && typeof value === 'object') {
    objectFallback = value as Record<string, unknown>
  } else {
    primitive = value
  }

  const headingCls = depth <= 1
    ? 'text-lg font-semibold text-slate-100'
    : depth === 2
      ? 'text-base font-semibold text-slate-200'
      : 'text-sm font-semibold text-slate-300'

  const indentCls = depth <= 1 ? '' : 'pl-3 border-l border-slate-800'
  const count = items?.length ?? arrayItems?.length

  return (
    <section className={`mb-4 ${indentCls}`}>
      <button
        onClick={() => setOpen(s => !s)}
        className={`flex items-baseline gap-2 ${headingCls} hover:text-white text-left w-full`}
      >
        <span className="text-slate-500 text-xs">{open ? '▾' : '▸'}</span>
        <span>{heading}</span>
        {count !== undefined && <span className="text-xs font-normal text-slate-500">({count})</span>}
      </button>

      {open && (
        <div className="mt-2 space-y-3">
          {description && (
            <p className="text-sm text-slate-400 leading-relaxed">{description}</p>
          )}

          {primitive !== undefined && (
            <p className="text-sm text-slate-300">{String(primitive)}</p>
          )}

          {items && <ItemList items={items} />}
          {arrayItems && <ItemList items={arrayItems} />}

          {objectFallback && (
            <div className="space-y-1">
              {Object.entries(objectFallback)
                .filter(([, v]) => v !== null && v !== '')
                .map(([k, v]) => (
                  <FrameSection key={k} name={k} value={v} depth={depth + 1} defaultOpen={depth < 2} />
                ))}
            </div>
          )}

          {subsections && (
            <div className="space-y-2">
              {Object.entries(subsections).map(([k, v]) => (
                <FrameSection key={k} name={k} value={v} depth={depth + 1} defaultOpen={depth < 2} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// Top-level header: paper metadata + domain rendered as a wiki article header.
function FrameHeader({ paper, domain }: { paper?: unknown; domain?: unknown }) {
  const p = (paper && typeof paper === 'object') ? paper as Record<string, unknown> : {}
  const title = typeof p.title === 'string' ? p.title : undefined
  const authors = Array.isArray(p.authors) ? (p.authors as unknown[]).map(String) : []
  const journal = typeof p.journal === 'string' ? p.journal : undefined
  const year = p.year !== null && p.year !== undefined && p.year !== '' ? String(p.year) : undefined
  const doi = typeof p.doi === 'string' ? p.doi : undefined
  const meta = [journal, year].filter(Boolean).join(' · ')

  return (
    <header className="border-b border-slate-700 pb-3 mb-3">
      {title && <h1 className="text-xl font-semibold text-slate-100 leading-snug">{title}</h1>}
      {authors.length > 0 && (
        <p className="text-sm text-slate-400 mt-1">{authors.join(', ')}</p>
      )}
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
        {meta && <span>{meta}</span>}
        {doi && <span>DOI: <span className="text-slate-400">{doi}</span></span>}
        {typeof domain === 'string' && domain && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-teal-700/50 bg-teal-900/30 text-teal-300">
            {domain}
          </span>
        )}
      </div>
    </header>
  )
}

function KnowledgeFrameTab({ projectId }: { projectId: string }) {
  const [frame, setFrame] = useState<Frame | null>(null)
  const [history, setHistory] = useState<ExtractionPass[]>([])
  const [loading, setLoading] = useState(true)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    Promise.all([getFrame(projectId), getFrameHistory(projectId)])
      .then(([f, h]) => { setFrame(f); setHistory(h as unknown as ExtractionPass[]) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (!frame) return <p className="text-sm text-slate-400">No knowledge frame yet. Run Extract to generate one.</p>

  const { content, extraction_summary, extraction_version, status, extracted_at, agent_annotations } = frame
  const clarifications = agent_annotations?.clarifications ?? []
  const resolvedFeedback = agent_annotations?.resolved_feedback ?? []

  return (
    <div className="space-y-4">
      <div className="flex gap-6 flex-wrap text-sm">
        <div><span className="text-slate-400">Status: </span><StatusBadge status={status} /></div>
        <div><span className="text-slate-400">Version: </span><span className="text-slate-200">v{extraction_version}</span></div>
        {extracted_at && <div><span className="text-slate-400">Extracted: </span><span className="text-slate-200">{extracted_at.slice(0, 10)}</span></div>}
      </div>

      {extraction_summary && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-400">
          {extraction_summary}
        </div>
      )}

      {history.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-400 mb-1">Extraction passes</p>
          <div className="space-y-0.5">
            {history.map((h, i) => (
              <div key={i} className="text-xs text-slate-500 px-2 py-1 bg-slate-900 rounded">
                Pass {h.pass_number} ({h.pass_type}) — {h.created_at.slice(0, 10)}
                {h.changes_made ? ' · changes made' : ''}
              </div>
            ))}
          </div>
        </div>
      )}

      {content && (
        <article className="max-w-3xl">
          <FrameHeader paper={(content as Record<string, unknown>).paper} domain={(content as Record<string, unknown>).domain} />
          {Object.entries(content)
            .filter(([k]) => k !== 'paper' && k !== 'domain')
            .map(([key, val]) => (
              <FrameSection key={key} name={key} value={val} depth={1} />
            ))}
        </article>
      )}

      {(clarifications.length > 0 || resolvedFeedback.length > 0) && (
        <div className="border-t border-slate-700 pt-3 space-y-2">
          <p className="text-xs font-medium text-slate-400">Agent Memory</p>
          {clarifications.length > 0 && (
            <details>
              <summary className="text-xs text-teal-400 cursor-pointer">Clarifications ({clarifications.length})</summary>
              <div className="mt-1 space-y-1 pl-2">
                {clarifications.map((c, i) => {
                  const cf = c as Record<string, string>
                  return (
                    <div key={i} className="text-xs text-slate-400 bg-slate-900 rounded px-2 py-1.5">
                      <p><span className="text-slate-500">Q ({cf.field ?? 'general'}):</span> {cf.question ?? ''}</p>
                      <p><span className="text-slate-500">A:</span> {cf.summary ?? ''}</p>
                    </div>
                  )
                })}
              </div>
            </details>
          )}
          {resolvedFeedback.length > 0 && (
            <details>
              <summary className="text-xs text-teal-400 cursor-pointer">Resolved Feedback ({resolvedFeedback.length})</summary>
              <div className="mt-1 space-y-1 pl-2">
                {resolvedFeedback.map((r, i) => {
                  const rf = r as Record<string, string>
                  return (
                    <div key={i} className="text-xs text-slate-400 bg-slate-900 rounded px-2 py-1.5">
                      <p>[{rf.status}] {rf.category ?? ''} ({rf.field_path ?? 'general'}): {rf.question ?? ''}</p>
                      <p><span className="text-slate-500">Resolution:</span> {rf.resolution_notes ?? ''}</p>
                    </div>
                  )
                })}
              </div>
            </details>
          )}
        </div>
      )}

      <div>
        <button onClick={() => setShowRaw(s => !s)} className="text-xs text-teal-400 hover:text-teal-300">
          {showRaw ? '▲ Hide raw JSON' : '▼ Show raw JSON'}
        </button>
        {showRaw && (
          <pre className="mt-2 bg-slate-900 border border-slate-700 rounded-lg p-4 text-xs text-slate-400 overflow-x-auto max-h-96">
            {JSON.stringify(frame, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}

// ─── Tab: Projections ────────────────────────────────────────────────────────

function ProjectionsTab({ projectId }: { projectId: string }) {
  const [projections, setProjections] = useState<Projection[]>([])
  const [spaces, setSpaces] = useState<Space[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      listProjections({ project_id: projectId, include_data: true, limit: 50 }),
      listSpaces(),
    ]).then(([projs, sps]) => {
      setProjections(projs)
      setSpaces(sps.filter(s => s.name !== '__global_kg__'))
    }).finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (projections.length === 0) return <p className="text-sm text-slate-400">No projections yet. Select a space and run Project.</p>

  const spaceMap: Record<string, string> = {}
  spaces.forEach(s => { spaceMap[s.space_id] = s.name })

  return (
    <div className="space-y-3">
      {projections.map(p => (
        <details key={p.projection_id} className="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
          <summary className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-slate-800">
            <StatusBadge status={p.status} />
            <span className="text-sm text-slate-200 font-medium">{spaceMap[p.space_id] ?? p.space_id.slice(0, 12)}</span>
            <span className="text-xs text-slate-400 ml-auto">
              v{p.space_version} · {p.times_reviewed}× reviewed · {p.extracted_at?.slice(0, 10) ?? '—'}
            </span>
          </summary>
          <div className="px-4 pb-4 pt-2 border-t border-slate-700 space-y-3">
            {p.agent_notes && <p className="text-xs text-slate-400 italic">{p.agent_notes.slice(0, 300)}</p>}
            {p.review_notes && (
              <div className="bg-teal-900/30 border border-teal-700/40 rounded px-3 py-2 text-xs text-teal-200">{p.review_notes}</div>
            )}
            {p.data && (
              <div className="space-y-2">
                {Object.entries(p.data).map(([section, value]) => (
                  <FrameSection key={section} name={section} value={value} depth={2} defaultOpen={true} />
                ))}
              </div>
            )}
          </div>
        </details>
      ))}
    </div>
  )
}

// ─── Tab: Knowledge Graph ─────────────────────────────────────────────────────

function GraphTab({ projectId }: { projectId: string }) {
  const [concepts, setConcepts] = useState<GraphConcept[]>([])
  const [relations, setRelations] = useState<GraphRelation[]>([])
  const [loading, setLoading] = useState(true)
  const [showList, setShowList] = useState(false)

  useEffect(() => {
    getKnowledgeGraph({ project_id: projectId })
      .then(d => { setConcepts(d.graph?.concepts ?? []); setRelations(d.graph?.relations ?? []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (concepts.length === 0) return <p className="text-sm text-slate-400">No graph elements for this project yet. Run Extract Graph.</p>

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">{concepts.length} concept(s), {relations.length} relation(s)</p>
      <MiniGraph concepts={concepts} relations={relations} />
      <div>
        <button onClick={() => setShowList(s => !s)} className="text-xs text-teal-400 hover:text-teal-300">
          {showList ? '▲ Hide list' : '▼ Show concept / relation list'}
        </button>
        {showList && (
          <div className="mt-2 grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs font-medium text-slate-400 mb-1">Concepts ({concepts.length})</p>
              <div className="space-y-0.5 max-h-48 overflow-y-auto">
                {concepts.map((c, i) => (
                  <div key={i} className="text-xs text-slate-400 px-2 py-1 bg-slate-900 rounded">
                    <span className="text-slate-200">{c.label}</span>
                    {c.aliases && c.aliases.length > 0 && <span className="text-slate-500 ml-1"> ({c.aliases.slice(0, 2).join(', ')})</span>}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-400 mb-1">Relations ({relations.length})</p>
              <div className="space-y-0.5 max-h-48 overflow-y-auto">
                {relations.map((r, i) => (
                  <div key={i} className="text-xs text-slate-500 px-2 py-1 bg-slate-900 rounded">
                    <span className="text-slate-300">{r.source}</span>
                    <span className="text-slate-500 mx-1">→ {r.relation} →</span>
                    <span className="text-slate-300">{r.target}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Tab: Feedback ────────────────────────────────────────────────────────────

function FeedbackTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<FeedbackItem[]>([])
  const [loading, setLoading] = useState(true)
  const [resolvingId, setResolvingId] = useState<string | null>(null)
  const [resolution, setResolution] = useState<'RESOLVED' | 'DISMISSED'>('RESOLVED')
  const [notes, setNotes] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    listFeedback({ project_id: projectId, limit: 100 })
      .then(setItems).catch(() => {}).finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load() }, [load])

  const submit = async (id: string) => {
    await resolveFeedback(id, resolution, notes)
    setResolvingId(null); setNotes(''); load()
  }

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (items.length === 0) return <p className="text-sm text-slate-400">No feedback items for this project.</p>

  return (
    <div className="space-y-2">
      {items.map(item => (
        <div key={item.feedback_id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5">
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <StatusBadge status={item.status} />
                <span className="text-xs text-slate-400 bg-slate-700 px-1.5 py-0.5 rounded">{item.category}</span>
                {item.field_path && <span className="text-xs text-slate-500 font-mono">{item.field_path}</span>}
              </div>
              <p className="text-sm text-slate-300">{item.question}</p>
              {item.context && <p className="text-xs text-slate-500 mt-0.5 italic">{item.context.slice(0, 120)}…</p>}
              {item.resolution_notes && <p className="text-xs text-teal-400 mt-0.5">{item.resolution_notes}</p>}
            </div>
            {item.status === 'OPEN' && resolvingId !== item.feedback_id && (
              <button onClick={() => setResolvingId(item.feedback_id)}
                className="flex-shrink-0 px-2 py-1 bg-slate-700 hover:bg-slate-600 text-xs text-slate-300 rounded">
                Resolve
              </button>
            )}
          </div>
          {resolvingId === item.feedback_id && (
            <div className="mt-2 space-y-2 border-t border-slate-700 pt-2">
              <div className="flex gap-2">
                {(['RESOLVED', 'DISMISSED'] as const).map(s => (
                  <button key={s} onClick={() => setResolution(s)}
                    className={`px-2 py-0.5 rounded text-xs font-medium ${resolution === s ? 'bg-teal-700 text-white' : 'bg-slate-700 text-slate-400'}`}>
                    {s}
                  </button>
                ))}
              </div>
              <textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Resolution notes…" rows={2}
                className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 resize-none focus:outline-none focus:border-teal-500" />
              <div className="flex gap-2">
                <button onClick={() => submit(item.feedback_id)} className="px-3 py-1 bg-teal-600 hover:bg-teal-500 text-white rounded text-xs">Save</button>
                <button onClick={() => setResolvingId(null)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs">Cancel</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ─── Full project detail ──────────────────────────────────────────────────────

type DetailTab = 'assets' | 'frame' | 'projections' | 'graph' | 'feedback'

function ProjectDetail({ project, onBack }: { project: Project; onBack: () => void }) {
  const [activeTab, setActiveTab] = useState<DetailTab>('frame')
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [projectionSource, setProjectionSource] = useState<'frame' | 'markdown'>('frame')
  const [feedbackCount, setFeedbackCount] = useState(0)
  // Bumped whenever a background job completes; passed as a key into the
  // active tab so its useEffect-driven fetches re-run automatically.
  const [refreshKey, setRefreshKey] = useState(0)

  const refreshFeedbackCount = useCallback(() => {
    listFeedback({ project_id: project.project_id, limit: 100 })
      .then(items => setFeedbackCount(items.length)).catch(() => {})
  }, [project.project_id])

  useEffect(() => {
    listSpaces().then(sps => {
      const visible = sps.filter(s => s.name !== '__global_kg__')
      setSpaces(visible)
      if (visible.length > 0 && !selectedSpaceId) setSelectedSpaceId(visible[0].space_id)
    }).catch(() => {})
    refreshFeedbackCount()
  }, [project.project_id, refreshFeedbackCount])

  const pollJob = useCallback((jobId: string, onDone?: () => void) => {
    setActiveJobId(jobId)
    const poll = async () => {
      try {
        const j = await getJob(jobId)
        setActiveJob(j)
        if (j.status === 'RUNNING' || j.status === 'PENDING') {
          setTimeout(poll, 1000)
        } else {
          setActiveJobId(null)
          if (j.status === 'COMPLETED') {
            onDone?.()
            // Force tabs that depend on backend data to refetch.
            setRefreshKey(k => k + 1)
            refreshFeedbackCount()
          }
        }
      } catch { setTimeout(poll, 2000) }
    }
    setTimeout(poll, 500)
  }, [refreshFeedbackCount])

  const run = async (fn: () => Promise<{ job_id: string }>, onDone?: () => void) => {
    if (activeJobId) return
    try { const { job_id } = await fn(); pollJob(job_id, onDone) } catch { /* ignore */ }
  }

  const label = project.label ?? project.source_path ?? project.project_id.slice(0, 12)

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 py-4 border-b border-slate-700 flex-shrink-0 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <button onClick={onBack} className="text-sm text-teal-400 hover:text-teal-300 flex-shrink-0">← Back</button>
            <h3 className="text-base font-semibold text-slate-100 truncate">{label}</h3>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <StatusBadge status={project.processing_status ?? 'UNPROCESSED'} />
            <StatusBadge status={project.frame_status ?? 'NO_FRAME'} />
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => run(() => processProject(project.project_id))} disabled={!!activeJobId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            ⚙ Process
          </button>
          <button onClick={() => run(() => extractProject(project.project_id))} disabled={!!activeJobId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            🧪 Extract
          </button>
          <select value={selectedSpaceId} onChange={e => setSelectedSpaceId(e.target.value)}
            disabled={spaces.length === 0 || !!activeJobId}
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none disabled:opacity-40">
            {spaces.length === 0 ? <option>No spaces</option>
              : spaces.map(s => <option key={s.space_id} value={s.space_id}>{s.name}</option>)}
          </select>
          <select value={projectionSource} onChange={e => setProjectionSource(e.target.value as 'frame' | 'markdown')}
            disabled={!!activeJobId}
            title="Project from the curated knowledge frame, or directly from the processed Markdown"
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none disabled:opacity-40">
            <option value="frame">from frame</option>
            <option value="markdown">from markdown</option>
          </select>
          <button onClick={() => run(() => projectToSpace(project.project_id, selectedSpaceId, projectionSource), () => setActiveTab('projections'))}
            disabled={!!activeJobId || !selectedSpaceId}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-xs text-slate-200">
            🗂 Project
          </button>
          <button onClick={() => run(() => kgExtractProject(project.project_id), () => setActiveTab('graph'))}
            disabled={!!activeJobId}
            className="px-3 py-1.5 bg-teal-700 hover:bg-teal-600 disabled:opacity-40 rounded text-xs text-white">
            🕸 Extract Graph
          </button>
        </div>
        {activeJob && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            {activeJobId && <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />}
            <span>{activeJob.current_message ?? activeJob.label}</span>
            {activeJob.status === 'COMPLETED' && <span className="text-green-400">✓ Done</span>}
            {activeJob.status === 'FAILED' && <span className="text-red-400">✗ {activeJob.error?.slice(0, 80)}</span>}
          </div>
        )}
      </div>

      <div className="px-6 pt-3 border-b border-slate-700 flex gap-1 flex-shrink-0 overflow-x-auto">
        {([
          ['assets', 'Assets'],
          ['frame', 'Knowledge Frame'],
          ['projections', 'Projections'],
          ['graph', 'Knowledge Graph'],
          ['feedback', `Feedback${feedbackCount > 0 ? ` (${feedbackCount})` : ''}`],
        ] as [DetailTab, string][]).map(([tab, name]) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-3 py-1.5 text-xs font-medium rounded-t whitespace-nowrap transition-colors ${
              activeTab === tab ? 'text-teal-400 border-b-2 border-teal-400 -mb-px' : 'text-slate-400 hover:text-slate-200'
            }`}>
            {name}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'assets'      && <AssetsTab key={`assets-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'frame'       && <KnowledgeFrameTab key={`frame-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'projections' && <ProjectionsTab key={`proj-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'graph'       && <GraphTab key={`graph-${refreshKey}`} projectId={project.project_id} />}
        {activeTab === 'feedback'    && <FeedbackTab key={`fb-${refreshKey}`} projectId={project.project_id} />}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function FramesPage() {
  const [rows, setRows] = useState<Array<{ project: Project; status: string; version: number; extracted_at: string }>>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Project | null>(null)
  const [spaces, setSpaces] = useState<Space[]>([])
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [filterProcessing, setFilterProcessing] = useState<string | null>(null)
  const [filterFrame, setFilterFrame] = useState<string | null>(null)
  const lastClickedIdx = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [projects, frames] = await Promise.all([listProjects(200), listFrames()])
      const frameMap: Record<string, Frame> = {}
      frames.forEach(f => { frameMap[f.project_id] = f })
      setRows(projects.map(p => {
        const f = frameMap[p.project_id]
        return {
          project: p,
          status: f?.status ?? 'NO_FRAME',
          version: f?.extraction_version ?? 0,
          extracted_at: (f?.extracted_at ?? '').slice(0, 10) || '—',
        }
      }))
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { listSpaces().then(setSpaces).catch(() => {}) }, [])

  const allProjectsList = useMemo(() => rows.map(r => r.project), [rows])
  const getStatus = useCallback(
    (id: string) => rows.find(r => r.project.project_id === id)?.status ?? 'NO_FRAME',
    [rows],
  )

  const visibleRows = useMemo(() =>
    rows.filter(r => {
      if (filterProcessing && (r.project.processing_status ?? 'UNPROCESSED') !== filterProcessing) return false
      if (filterFrame && r.status !== filterFrame) return false
      return true
    }),
    [rows, filterProcessing, filterFrame],
  )

  const processingOptions = useMemo(() => {
    const counts: Record<string, number> = {}
    rows.forEach(r => { const s = r.project.processing_status ?? 'UNPROCESSED'; counts[s] = (counts[s] ?? 0) + 1 })
    return Object.entries(counts).sort()
  }, [rows])

  const frameOptions = useMemo(() => {
    const counts: Record<string, number> = {}
    rows.forEach(r => { counts[r.status] = (counts[r.status] ?? 0) + 1 })
    return Object.entries(counts).sort()
  }, [rows])

  const handleCheck = (idx: number, id: string, shift: boolean) => {
    if (shift && lastClickedIdx.current !== null) {
      const lo = Math.min(lastClickedIdx.current, idx)
      const hi = Math.max(lastClickedIdx.current, idx)
      setCheckedIds(prev => {
        const next = new Set(prev)
        const adding = !prev.has(id)
        for (let i = lo; i <= hi; i++) {
          if (i >= 0 && i < visibleRows.length) {
            if (adding) next.add(visibleRows[i].project.project_id)
            else next.delete(visibleRows[i].project.project_id)
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

  const allChecked = visibleRows.length > 0 && visibleRows.every(r => checkedIds.has(r.project.project_id))
  const toggleAll = () =>
    setCheckedIds(allChecked ? new Set() : new Set(visibleRows.map(r => r.project.project_id)))

  if (selected) return <ProjectDetail project={selected} onBack={() => { setSelected(null); load() }} />

  return (
    <div className="p-6 max-w-5xl">
      <h2 className="text-xl font-semibold mb-1">Knowledge Frames</h2>
      <p className="text-sm text-slate-400 mb-5">Browse and inspect extracted knowledge from each project.</p>
      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-slate-400 text-sm">No projects yet — upload files in the Projects tab.</p>
      ) : (
        <div className="space-y-4">
          {checkedIds.size > 0 && (
            <BatchActionBar
              selectedIds={checkedIds}
              allProjects={allProjectsList}
              spaces={spaces}
              getStatus={getStatus}
              onSelectionChange={ids => setCheckedIds(ids)}
              onRefresh={load}
            />
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
                onClick={() => setCheckedIds(new Set(visibleRows.map(r => r.project.project_id)))}
                className="text-teal-400 hover:text-teal-300 ml-1"
              >select all {visibleRows.length}</button>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400 uppercase tracking-wide border-b border-slate-700">
                  <th className="pb-2 pr-2 w-8">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      onChange={toggleAll}
                      className="accent-teal-500"
                      title="Select all"
                    />
                  </th>
                  <th className="pb-2 pr-3 font-medium">Processed</th>
                  <th className="pb-2 pr-3 font-medium">Frame</th>
                  <th className="pb-2 pr-3 font-medium">Label / Path</th>
                  <th className="pb-2 pr-3 font-medium">Assets</th>
                  <th className="pb-2 pr-3 font-medium">Version</th>
                  <th className="pb-2 pr-3 font-medium">Extracted</th>
                  <th className="pb-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {visibleRows.map((row, idx) => {
                  const checked = checkedIds.has(row.project.project_id)
                  return (
                    <tr key={row.project.project_id} className={checked ? 'bg-teal-900/15' : 'hover:bg-slate-800/50'}>
                      <td className="py-2 pr-2 w-8">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={e => handleCheck(idx, row.project.project_id, (e.nativeEvent as MouseEvent).shiftKey)}
                          className="accent-teal-500"
                        />
                      </td>
                      <td className="py-2 pr-3"><StatusBadge status={row.project.processing_status ?? 'UNPROCESSED'} /></td>
                      <td className="py-2 pr-3"><StatusBadge status={row.status} /></td>
                      <td className="py-2 pr-3 text-slate-200 max-w-sm truncate">
                        {row.project.label ?? row.project.source_path ?? row.project.project_id.slice(0, 12)}
                      </td>
                      <td className="py-2 pr-3 text-slate-400">{row.project.asset_count}</td>
                      <td className="py-2 pr-3 text-slate-400">v{row.version}</td>
                      <td className="py-2 pr-3 text-slate-500 text-xs">{row.extracted_at}</td>
                      <td className="py-2">
                        <button onClick={() => setSelected(row.project)}
                          className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs">
                          View
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
