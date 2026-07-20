import type { Options } from 'vis-network'

export const EDGE_COLORS: Record<number, string> = { 1: '#34d399', 2: '#60a5fa', 3: '#fbbf24', 4: '#f472b6' }
export type NodeColor = { background: string; border: string; highlight: { background: string; border: string }; hover: { background: string; border: string } }

export const lerpColor = (left: string, right: string, ratio: number) => {
  const rgb = (value: string) => [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)]
  const a = rgb(left); const b = rgb(right)
  return `#${a.map((value, index) => Math.round(value + (b[index] - value) * ratio).toString(16).padStart(2, '0')).join('')}`
}
export const coverageColor = (count: number, maximum: number) => {
  if (maximum <= 0 || count <= 0) return '#6b7280'
  const ratio = Math.min(count / maximum, 1)
  return ratio < .5 ? lerpColor('#6b7280', '#34d399', ratio * 2) : lerpColor('#34d399', '#f59e0b', (ratio - .5) * 2)
}
export const glowNode = (background: string, border: string): NodeColor => ({ background, border, highlight: { background, border: '#f8fafc' }, hover: { background, border } })
export const degreeGlow = (ratio: number) => glowNode(
  ratio < .5 ? lerpColor('#0c1a2e', '#0a1f18', ratio * 2) : lerpColor('#0a1f18', '#1f1005', (ratio - .5) * 2),
  ratio < .5 ? lerpColor('#3b82f6', '#10b981', ratio * 2) : lerpColor('#10b981', '#f97316', (ratio - .5) * 2),
)
export const seedPosition = (index: number, total: number) => {
  const radius = Math.max(400, Math.sqrt(total) * 80)
  const x = Math.sin(index * 12.9898) * 43758.5453; const y = Math.sin(index * 78.233) * 43758.5453
  return { x: ((x - Math.floor(x)) * 2 - 1) * radius, y: ((y - Math.floor(y)) * 2 - 1) * radius }
}
export function buildVisOptions(nodeCount: number, edgeCount: number): Options {
  const large = nodeCount > 400 || edgeCount > 1000
  const iterations = Math.min(800, Math.max(150, Math.round(nodeCount * 1.5)))
  const physics = large ? { solver: 'forceAtlas2Based' as const, forceAtlas2Based: { gravitationalConstant: -55, centralGravity: .008, springLength: 120, springConstant: .05, damping: .5, avoidOverlap: .6 }, maxVelocity: 35, minVelocity: .75, timestep: .5, stabilization: { enabled: true, iterations, updateInterval: 25, fit: true } } : { solver: 'barnesHut' as const, barnesHut: { gravitationalConstant: -6000, centralGravity: .3, springLength: 110, springConstant: .05, damping: .12, avoidOverlap: .5 }, stabilization: { enabled: true, iterations, updateInterval: 25, fit: true } }
  return { layout: { improvedLayout: !large, randomSeed: 42 }, physics, nodes: { font: { size: 0 }, borderWidth: 2, borderWidthSelected: 3, shape: 'dot', shadow: { enabled: true, color: 'rgba(0, 0, 0, 0.45)', size: 12, x: 0, y: 2 }, scaling: { min: 10, max: 40 }, chosen: { node: ((values: { borderWidth: number; shadowSize: number; shadowColor: string }) => { values.borderWidth = 3; values.shadowSize = 22; values.shadowColor = 'rgba(45, 212, 191, 0.55)' }) as unknown as boolean, label: false } }, edges: { font: { size: 0 }, smooth: { enabled: !large, type: 'continuous', roundness: .25, forceDirection: 'none' }, width: 1.2, selectionWidth: 1.5, arrows: { to: { enabled: true, scaleFactor: .45, type: 'arrow' } }, arrowStrikethrough: false, hoverWidth: .6 }, interaction: { hover: true, tooltipDelay: 80, hideEdgesOnDrag: false, hideNodesOnDrag: false, navigationButtons: false, multiselect: false, dragView: true, zoomView: true } }
}
