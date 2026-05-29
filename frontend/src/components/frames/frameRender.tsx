import { Fragment, useState } from 'react'

// Shared "wiki-style" rendering used by both KnowledgeFrameTab and
// ProjectionsTab. Kept in one module so the recursive FrameSection /
// ItemList / ItemCard / InlineValue stack lives in a single place.

// Evidence level → small coloured pill.
const EVIDENCE_META: Record<number, { label: string; cls: string }> = {
  1: { label: 'L1 · causal',      cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50' },
  2: { label: 'L2 · observed',    cls: 'bg-sky-900/50 text-sky-300 border-sky-700/50' },
  3: { label: 'L3 · correlative', cls: 'bg-amber-900/40 text-amber-300 border-amber-700/50' },
  4: { label: 'L4 · inferred',    cls: 'bg-orange-900/40 text-orange-300 border-orange-700/50' },
}

export function EvidenceBadge({ level }: { level: number }) {
  const meta = EVIDENCE_META[level]
  if (!meta) return null
  return (
    <span className={`inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border ${meta.cls}`}>
      {meta.label}
    </span>
  )
}

export const prettyKey = (k: string) =>
  k.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

// A "section block" is the shape the extraction prompt may emit:
//   { heading, description?, items?, subsections? }
export function isSectionBlock(v: unknown): v is {
  heading?: string; description?: string;
  items?: unknown[]; subsections?: Record<string, unknown>;
} {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false
  const o = v as Record<string, unknown>
  return ('heading' in o || 'description' in o) && ('items' in o || 'subsections' in o)
}

// Render any value inline (used inside item-detail rows).
export function InlineValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') return <span className="text-slate-600">—</span>
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <span>{String(value)}</span>
  }
  if (Array.isArray(value)) {
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
export function ItemCard({ item }: { item: Record<string, unknown> }) {
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
export function ItemList({ items }: { items: unknown[] }) {
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
export function FrameSection({
  name, value, depth = 1, defaultOpen = true,
}: { name: string; value: unknown; depth?: number; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

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
export function FrameHeader({ paper, domain }: { paper?: unknown; domain?: unknown }) {
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
