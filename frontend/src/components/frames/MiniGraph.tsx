import { useEffect, useRef } from 'react'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'

import type { GraphConcept, GraphRelation } from '../../types'


export default function MiniGraph({
  concepts,
  relations,
}: {
  concepts: GraphConcept[]
  relations: GraphRelation[]
}) {
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
