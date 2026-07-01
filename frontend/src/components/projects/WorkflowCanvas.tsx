import { useEffect, useState } from 'react'
import dagre from '@dagrejs/dagre'
import ReactFlow, {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  applyNodeChanges,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from 'reactflow'
import 'reactflow/dist/style.css'

type WorkflowNodeKind = 'object' | 'operation' | 'planning' | 'reasoning' | 'unknown'

export interface WorkflowCanvasNode {
  id: string
  label: string
  kind: WorkflowNodeKind
  title?: string
  details?: Record<string, unknown>
}

export interface WorkflowCanvasEdge {
  id: string
  source: string
  target: string
  label?: string
  title?: string
}

interface WorkflowNodeData {
  id: string
  kind: WorkflowNodeKind
  label: string
  title?: string
  details?: Record<string, unknown>
}

interface WorkflowEdgeData {
  title?: string
  points?: Array<{ x: number; y: number }>
  sourceSide?: AnchorSide
  targetSide?: AnchorSide
}

const XML_NS = 'http://www.w3.org/2000/svg'
const OP_WIDTH = 190
const OBJ_WIDTH = 190
const CONTEXT_WIDTH = 210
const NODE_HEIGHT = 74

type AnchorSide = 'top' | 'right' | 'bottom' | 'left'

function NodeHandles() {
  const handles: AnchorSide[] = ['top', 'right', 'bottom', 'left']
  return (
    <>
      {handles.map(side => (
        <Handle
          key={`target-${side}`}
          id={`target-${side}`}
          type="target"
          position={sideToPosition(side)}
          className="opacity-0"
        />
      ))}
      {handles.map(side => (
        <Handle
          key={`source-${side}`}
          id={`source-${side}`}
          type="source"
          position={sideToPosition(side)}
          className="opacity-0"
        />
      ))}
    </>
  )
}

function LabelText({ label }: { label: string }) {
  return (
    <div
      className="text-center text-[12px] font-medium leading-snug text-slate-100"
      style={{
        display: '-webkit-box',
        WebkitBoxOrient: 'vertical',
        WebkitLineClamp: 3,
        overflow: 'hidden',
        wordBreak: 'break-word',
      }}
    >
      {label}
    </div>
  )
}

function OperationNode({ data }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      title={data.title}
      className="rounded-xl border border-violet-400/70 bg-violet-900/80 px-4 py-3 shadow-[0_10px_30px_rgba(76,29,149,0.25)]"
      style={{ width: OP_WIDTH }}
    >
      <NodeHandles />
      <LabelText label={data.label} />
    </div>
  )
}

function ObjectNode({ data }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      title={data.title}
      className="border border-teal-300/70 bg-teal-900/75 px-4 py-3 shadow-[0_10px_28px_rgba(13,148,136,0.22)]"
      style={{
        width: OBJ_WIDTH,
        transform: 'skew(-18deg)',
        borderRadius: 12,
      }}
    >
      <NodeHandles />
      <div style={{ transform: 'skew(18deg)' }}>
        <LabelText label={data.label} />
      </div>
    </div>
  )
}

function PlanningNode({ data }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      title={data.title}
      className="rounded-lg border border-amber-300/75 bg-amber-900/75 px-4 py-3 shadow-[0_10px_28px_rgba(217,119,6,0.2)]"
      style={{ width: CONTEXT_WIDTH }}
    >
      <NodeHandles />
      <LabelText label={data.label} />
    </div>
  )
}

function ReasoningNode({ data }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      title={data.title}
      className="rounded-lg border border-sky-300/75 bg-sky-900/75 px-4 py-3 shadow-[0_10px_28px_rgba(14,116,144,0.2)]"
      style={{ width: CONTEXT_WIDTH }}
    >
      <NodeHandles />
      <LabelText label={data.label} />
    </div>
  )
}

function UnknownNode({ data }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      title={data.title}
      className="rounded-lg border border-slate-400/70 bg-slate-800/85 px-4 py-3 shadow-[0_10px_28px_rgba(15,23,42,0.22)]"
      style={{ width: CONTEXT_WIDTH }}
    >
      <NodeHandles />
      <LabelText label={data.label} />
    </div>
  )
}

function sideToPosition(side: AnchorSide) {
  switch (side) {
    case 'top':
      return Position.Top
    case 'right':
      return Position.Right
    case 'bottom':
      return Position.Bottom
    case 'left':
      return Position.Left
  }
}

function sideVector(side: AnchorSide) {
  switch (side) {
    case 'top':
      return { x: 0, y: -1 }
    case 'right':
      return { x: 1, y: 0 }
    case 'bottom':
      return { x: 0, y: 1 }
    case 'left':
      return { x: -1, y: 0 }
  }
}

function escapeXml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function labelLines(label: string, maxChars = 18, maxLines = 3) {
  const words = label.split(/\s+/).filter(Boolean)
  if (words.length === 0) return ['']

  const lines: string[] = []
  let current = ''
  let index = 0

  while (index < words.length) {
    const word = words[index]
    const candidate = current ? `${current} ${word}` : word
    if (candidate.length <= maxChars || current.length === 0) {
      current = candidate
      index += 1
      continue
    }

    lines.push(current)
    current = word
    index += 1
    if (lines.length === maxLines - 1) {
      break
    }
  }

  const tailWords = current ? [current, ...words.slice(index)] : words.slice(index)
  const tail = tailWords.join(' ').trim()
  if (tail) {
    lines.push(tail)
  }

  if (lines.length > maxLines) {
    lines.length = maxLines
  }
  if (lines.length === maxLines && lines[maxLines - 1].length > maxChars + 6) {
    lines[maxLines - 1] = `${lines[maxLines - 1].slice(0, maxChars + 3).trimEnd()}...`
  }
  return lines
}

function nodeWidth(kind: WorkflowNodeKind) {
  if (kind === 'operation') return OP_WIDTH
  if (kind === 'object') return OBJ_WIDTH
  return CONTEXT_WIDTH
}

function nodeBounds(node: Node<WorkflowNodeData>) {
  const kind = (node.type ?? node.data.kind) as WorkflowNodeKind
  const width = nodeWidth(kind)
  return {
    x: node.position.x,
    y: node.position.y,
    width,
    height: NODE_HEIGHT,
    centerX: node.position.x + width / 2,
    centerY: node.position.y + NODE_HEIGHT / 2,
  }
}

function sidePoint(box: ReturnType<typeof nodeBounds>, side: AnchorSide) {
  switch (side) {
    case 'top':
      return { x: box.centerX, y: box.y }
    case 'right':
      return { x: box.x + box.width, y: box.centerY }
    case 'bottom':
      return { x: box.centerX, y: box.y + box.height }
    case 'left':
      return { x: box.x, y: box.centerY }
  }
}

function chooseAnchorSides(source: Node<WorkflowNodeData>, target: Node<WorkflowNodeData>) {
  const sb = nodeBounds(source)
  const tb = nodeBounds(target)
  const sourceSides: AnchorSide[] = ['top', 'right', 'bottom', 'left']
  const targetSides: AnchorSide[] = ['top', 'right', 'bottom', 'left']
  let best = { sourceSide: 'bottom' as AnchorSide, targetSide: 'top' as AnchorSide, score: Number.POSITIVE_INFINITY }

  sourceSides.forEach(sourceSide => {
    targetSides.forEach(targetSide => {
      const start = sidePoint(sb, sourceSide)
      const end = sidePoint(tb, targetSide)
      const dx = end.x - start.x
      const dy = end.y - start.y
      let score = Math.abs(dx) + Math.abs(dy)

      const sv = sideVector(sourceSide)
      const tv = sideVector(targetSide)
      if (Math.sign(dx) !== 0 && Math.sign(dx) !== Math.sign(sv.x)) score += sourceSide === 'left' || sourceSide === 'right' ? 140 : 40
      if (Math.sign(dy) !== 0 && Math.sign(dy) !== Math.sign(sv.y)) score += sourceSide === 'top' || sourceSide === 'bottom' ? 140 : 40
      if (Math.sign(dx) !== 0 && Math.sign(dx) === Math.sign(tv.x)) score += targetSide === 'left' || targetSide === 'right' ? 140 : 40
      if (Math.sign(dy) !== 0 && Math.sign(dy) === Math.sign(tv.y)) score += targetSide === 'top' || targetSide === 'bottom' ? 140 : 40

      const mostlyVertical = Math.abs(dy) > Math.abs(dx) * 0.9
      const mostlyHorizontal = Math.abs(dx) > Math.abs(dy) * 0.9
      if (mostlyVertical && sourceSide === 'bottom' && targetSide === 'top' && dy > 0) score -= 130
      if (mostlyVertical && sourceSide === 'top' && targetSide === 'bottom' && dy < 0) score -= 130
      if (mostlyHorizontal && sourceSide === 'right' && targetSide === 'left' && dx > 0) score -= 130
      if (mostlyHorizontal && sourceSide === 'left' && targetSide === 'right' && dx < 0) score -= 130
      if (sourceSide === targetSide) score += 1000

      if (sourceSide === 'bottom') score -= 90
      if (sourceSide === 'right') score -= 35
      if (sourceSide === 'top') score += 220
      if (sourceSide === 'left') score += 70

      if (targetSide === 'top') score -= 90
      if (targetSide === 'left') score -= 35
      if (targetSide === 'bottom') score += 220
      if (targetSide === 'right') score += 70

      if (score < best.score) {
        best = { sourceSide, targetSide, score }
      }
    })
  })

  return best
}

function edgePath(source: Node<WorkflowNodeData>, target: Node<WorkflowNodeData>) {
  const sb = nodeBounds(source)
  const tb = nodeBounds(target)
  const { sourceSide, targetSide } = chooseAnchorSides(source, target)
  const start = sidePoint(sb, sourceSide)
  const end = sidePoint(tb, targetSide)
  const sv = sideVector(sourceSide)
  const tv = sideVector(targetSide)
  const distance = Math.max(72, Math.min(220, (Math.abs(end.x - start.x) + Math.abs(end.y - start.y)) / 2))
  const c1 = { x: start.x + sv.x * distance, y: start.y + sv.y * distance }
  const c2 = { x: end.x + tv.x * distance, y: end.y + tv.y * distance }
  return `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`
}

function downloadFile(filename: string, mimeType: string, content: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function dedupePoints(points: Array<{ x: number; y: number }>) {
  return points.filter((point, index) => {
    const previous = points[index - 1]
    return !previous || Math.abs(previous.x - point.x) > 1 || Math.abs(previous.y - point.y) > 1
  })
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.hypot(b.x - a.x, b.y - a.y)
}

function midpointOnPolyline(points: Array<{ x: number; y: number }>) {
  if (points.length === 0) return { x: 0, y: 0 }
  if (points.length === 1) return points[0]

  const total = points.slice(1).reduce((sum, point, index) => sum + distance(points[index], point), 0)
  const target = total / 2
  let traversed = 0

  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1]
    const end = points[index]
    const segment = distance(start, end)
    if (traversed + segment >= target) {
      const ratio = segment === 0 ? 0 : (target - traversed) / segment
      return {
        x: start.x + (end.x - start.x) * ratio,
        y: start.y + (end.y - start.y) * ratio,
      }
    }
    traversed += segment
  }

  return points[points.length - 1]
}

function controlDistance(start: { x: number; y: number }, end: { x: number; y: number }) {
  return Math.max(44, Math.min(150, distance(start, end) / 2))
}

function smoothBezierPath(points: Array<{ x: number; y: number }>, sourceSide?: AnchorSide, targetSide?: AnchorSide) {
  if (points.length === 0) return ''
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`
  if (points.length === 2) {
    const [start, end] = points
    const sourceVector = sideVector(sourceSide ?? 'bottom')
    const targetVector = sideVector(targetSide ?? 'top')
    const offset = controlDistance(start, end)
    const c1 = { x: start.x + sourceVector.x * offset, y: start.y + sourceVector.y * offset }
    const c2 = { x: end.x + targetVector.x * offset, y: end.y + targetVector.y * offset }
    return `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`
  }

  const parts = [`M ${points[0].x} ${points[0].y}`]
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[index - 1] ?? points[index]
    const start = points[index]
    const end = points[index + 1]
    const next = points[index + 2] ?? end
    const sourceVector = sourceSide ? sideVector(sourceSide) : null
    const targetVector = targetSide ? sideVector(targetSide) : null
    let c1 = {
      x: start.x + (end.x - previous.x) / 6,
      y: start.y + (end.y - previous.y) / 6,
    }
    let c2 = {
      x: end.x - (next.x - start.x) / 6,
      y: end.y - (next.y - start.y) / 6,
    }
    if (index === 0 && sourceVector) {
      const offset = controlDistance(start, end)
      c1 = { x: start.x + sourceVector.x * offset, y: start.y + sourceVector.y * offset }
    }
    if (index === points.length - 2 && targetVector) {
      const offset = controlDistance(start, end)
      c2 = { x: end.x + targetVector.x * offset, y: end.y + targetVector.y * offset }
    }
    parts.push(`C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`)
  }
  return parts.join(' ')
}

function WorkflowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  data,
  label,
}: EdgeProps<WorkflowEdgeData>) {
  const points = dedupePoints([
    { x: sourceX, y: sourceY },
    ...(data?.points ?? []),
    { x: targetX, y: targetY },
  ])
  const path = smoothBezierPath(points, data?.sourceSide, data?.targetSide)
  const labelPoint = midpointOnPolyline(points)

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan rounded-md bg-zinc-950/95 px-1.5 py-0.5 text-[11px] text-slate-400"
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelPoint.x}px, ${labelPoint.y}px)`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
}

function anchorEdges(edgeList: Edge[], nodeList: Node<WorkflowNodeData>[]) {
  const flowNodeMap = new Map(nodeList.map(node => [node.id, node]))
  return edgeList.map(edge => {
    const source = flowNodeMap.get(String(edge.source))
    const target = flowNodeMap.get(String(edge.target))
    const anchors = source && target ? chooseAnchorSides(source, target) : null
    return {
      ...edge,
      sourceHandle: anchors ? `source-${anchors.sourceSide}` : edge.sourceHandle,
      targetHandle: anchors ? `target-${anchors.targetSide}` : edge.targetHandle,
      data: anchors ? { ...(edge.data ?? {}), sourceSide: anchors.sourceSide, targetSide: anchors.targetSide } : edge.data,
    }
  })
}

function buildLayout(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]): { nodes: Node<WorkflowNodeData>[]; edges: Edge[] } {
  if (nodes.length === 0) return { nodes: [], edges: [] }

  const nodeMap = new Map(nodes.map(node => [node.id, node]))
  const validEdges = edges.filter(edge => nodeMap.has(edge.source) && nodeMap.has(edge.target))
  const dagreGraph = new dagre.graphlib.Graph({ multigraph: true })
  dagreGraph.setDefaultEdgeLabel(() => ({}))
  dagreGraph.setGraph({
    rankdir: 'TB',
    align: 'UL',
    nodesep: 62,
    edgesep: 34,
    ranksep: 96,
    marginx: 32,
    marginy: 32,
    acyclicer: 'greedy',
    ranker: 'network-simplex',
  })

  ;[...nodes]
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach(node => {
      dagreGraph.setNode(node.id, {
        width: nodeWidth(node.kind),
        height: NODE_HEIGHT,
      })
    })

  ;[...validEdges]
    .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    .forEach(edge => {
      dagreGraph.setEdge(
        edge.source,
        edge.target,
        {
          weight: 1,
          minlen: 1,
          width: edge.label ? Math.max(40, edge.label.length * 6) : 0,
          height: edge.label ? 18 : 0,
        },
        edge.id,
      )
    })

  dagre.layout(dagreGraph)

  const flowNodes: Node<WorkflowNodeData>[] = nodes.map(node => {
    const layoutNode = dagreGraph.node(node.id) as { x?: number; y?: number } | undefined
    const width = nodeWidth(node.kind)
    return {
      id: node.id,
      type: node.kind,
      position: {
        x: (layoutNode?.x ?? 0) - width / 2,
        y: (layoutNode?.y ?? 0) - NODE_HEIGHT / 2,
      },
      data: { id: node.id, kind: node.kind, label: node.label, title: node.title, details: node.details },
    }
  })

  const flowEdges: Edge[] = validEdges.map(edge => {
    const layoutEdge = dagreGraph.edge({ v: edge.source, w: edge.target, name: edge.id }) as { points?: Array<{ x: number; y: number }> } | undefined
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'workflow',
      label: edge.label,
      data: { title: edge.title, points: layoutEdge?.points ?? [] },
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: '#64748b' },
      style: { stroke: '#64748b', strokeWidth: 1.5 },
    }
  })

  return { nodes: flowNodes, edges: anchorEdges(flowEdges, flowNodes) }
}

const nodeTypes = {
  operation: OperationNode,
  object: ObjectNode,
  planning: PlanningNode,
  reasoning: ReasoningNode,
  unknown: UnknownNode,
}

const edgeTypes = {
  workflow: WorkflowEdge,
}

function nodeColor(kind: WorkflowNodeKind) {
  switch (kind) {
    case 'operation':
      return '#8b5cf6'
    case 'object':
      return '#14b8a6'
    case 'planning':
      return '#f59e0b'
    case 'reasoning':
      return '#38bdf8'
    case 'unknown':
      return '#64748b'
  }
}

export default function WorkflowCanvas({
  nodes,
  edges,
  exportBaseName = 'workflow',
}: {
  nodes: WorkflowCanvasNode[]
  edges: WorkflowCanvasEdge[]
  exportBaseName?: string
}) {
  const [flowNodes, setFlowNodes] = useNodesState<WorkflowNodeData>([])
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  useEffect(() => {
    const layout = buildLayout(nodes, edges)
    setFlowNodes(layout.nodes)
    setFlowEdges(layout.edges)
    setSelectedNodeId(null)
  }, [edges, nodes, setFlowEdges, setFlowNodes])

  const selectedNode = flowNodes.find(node => node.id === selectedNodeId) ?? null

  const handleNodesChange = (changes: NodeChange[]) => {
    setFlowNodes(currentNodes => {
      const nextNodes = applyNodeChanges(changes, currentNodes)
      setFlowEdges(currentEdges => anchorEdges(currentEdges.map(edge => ({
        ...edge,
        data: { ...(edge.data ?? {}), points: [] },
      })), nextNodes))
      return nextNodes
    })
  }

  const exportSvg = () => {
    if (flowNodes.length === 0) return

    const padding = 48
    const bounds = flowNodes.reduce(
      (acc, node) => {
        const box = nodeBounds(node)
        return {
          minX: Math.min(acc.minX, box.x),
          minY: Math.min(acc.minY, box.y),
          maxX: Math.max(acc.maxX, box.x + box.width),
          maxY: Math.max(acc.maxY, box.y + box.height),
        }
      },
      { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY, maxY: Number.NEGATIVE_INFINITY },
    )

    const minX = bounds.minX - padding
    const minY = bounds.minY - padding
    const width = bounds.maxX - bounds.minX + padding * 2
    const height = bounds.maxY - bounds.minY + padding * 2
    const nodeMap = new Map(flowNodes.map(node => [node.id, node]))

    const svgParts = [
      `<?xml version="1.0" encoding="UTF-8"?>`,
      `<svg xmlns="${XML_NS}" width="${width}" height="${height}" viewBox="${minX} ${minY} ${width} ${height}">`,
      `<defs>`,
      `<marker id="arrowhead" markerWidth="10" markerHeight="7" refX="8" refY="3.5" orient="auto">`,
      `<polygon points="0 0, 10 3.5, 0 7" fill="#64748b" />`,
      `</marker>`,
      `</defs>`,
      `<rect x="${minX}" y="${minY}" width="${width}" height="${height}" fill="#020617" rx="18" ry="18" />`,
    ]

    for (const edge of flowEdges) {
      const source = nodeMap.get(edge.source)
      const target = nodeMap.get(edge.target)
      if (!source || !target) continue
      const path = edgePath(source, target)
      svgParts.push(`<path d="${path}" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#arrowhead)" />`)
      if (edge.label) {
        const sb = nodeBounds(source)
        const tb = nodeBounds(target)
        const labelX = (sb.centerX + tb.centerX) / 2
        const labelY = (sb.centerY + tb.centerY) / 2
        svgParts.push(`<text x="${labelX}" y="${labelY}" fill="#94a3b8" font-size="11" text-anchor="middle">${escapeXml(String(edge.label))}</text>`)
      }
    }

    for (const node of flowNodes) {
      const box = nodeBounds(node)
      const lines = labelLines(node.data.label)
      const kind = node.data.kind
      if (kind === 'operation') {
        svgParts.push(
          `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="16" ry="16" fill="#4c1d95" fill-opacity="0.88" stroke="#a78bfa" stroke-width="1.5" />`,
        )
      } else if (kind === 'object') {
        const slant = 20
        const points = [
          `${box.x + slant},${box.y}`,
          `${box.x + box.width},${box.y}`,
          `${box.x + box.width - slant},${box.y + box.height}`,
          `${box.x},${box.y + box.height}`,
        ].join(' ')
        svgParts.push(
          `<polygon points="${points}" fill="#134e4a" fill-opacity="0.9" stroke="#5eead4" stroke-width="1.5" />`,
        )
      } else {
        const fill = kind === 'planning' ? '#78350f' : kind === 'reasoning' ? '#0c4a6e' : '#1e293b'
        const stroke = kind === 'planning' ? '#fcd34d' : kind === 'reasoning' ? '#7dd3fc' : '#94a3b8'
        svgParts.push(
          `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="8" ry="8" fill="${fill}" fill-opacity="0.9" stroke="${stroke}" stroke-width="1.5" />`,
        )
      }

      const startY = box.centerY - ((lines.length - 1) * 14) / 2
      lines.forEach((line, index) => {
        svgParts.push(
          `<text x="${box.centerX}" y="${startY + index * 14}" fill="#f1f5f9" font-size="12" font-weight="600" text-anchor="middle">${escapeXml(line)}</text>`,
        )
      })
    }

    svgParts.push(`</svg>`)
    downloadFile(`${exportBaseName}.svg`, 'image/svg+xml;charset=utf-8', svgParts.join(''))
  }

  const exportXml = () => {
    const xml = [
      `<?xml version="1.0" encoding="UTF-8"?>`,
      `<workflow export_name="${escapeXml(exportBaseName)}">`,
      `  <nodes>`,
      ...flowNodes.map(node => {
        const kind = node.data.kind
        return `    <node id="${escapeXml(node.id)}" kind="${kind}" x="${node.position.x}" y="${node.position.y}"><label>${escapeXml(node.data.label)}</label>${node.data.title ? `<title>${escapeXml(node.data.title)}</title>` : ''}</node>`
      }),
      `  </nodes>`,
      `  <edges>`,
      ...flowEdges.map(edge => (
        `    <edge id="${escapeXml(String(edge.id))}" source="${escapeXml(String(edge.source))}" target="${escapeXml(String(edge.target))}">${edge.label ? `<label>${escapeXml(String(edge.label))}</label>` : ''}</edge>`
      )),
      `  </edges>`,
      `</workflow>`,
    ].join('\n')

    downloadFile(`${exportBaseName}.xml`, 'application/xml;charset=utf-8', xml)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          onClick={exportSvg}
          disabled={flowNodes.length === 0}
          className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-xs text-slate-200 border border-slate-600"
        >
          Export SVG
        </button>
        <button
          onClick={exportXml}
          disabled={flowNodes.length === 0}
          className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-xs text-slate-200 border border-slate-600"
        >
          Export XML
        </button>
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="h-[680px] rounded-xl border border-slate-700 bg-slate-950/95">
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            minZoom={0.2}
            maxZoom={1.8}
            nodesConnectable={false}
            elementsSelectable
          >
            <Background color="#27272a" gap={24} />
            <MiniMap
              pannable
              zoomable
              nodeColor={node => nodeColor((node.data as WorkflowNodeData).kind)}
              maskColor="rgba(9, 9, 11, 0.78)"
            />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-900/75 p-4">
          {selectedNode ? (
            <div className="space-y-3">
              <div>
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Selected node</p>
                <h4 className="mt-1 text-sm font-semibold text-slate-100">{selectedNode.data.label}</h4>
                <p className="mt-1 text-xs text-slate-400">{selectedNode.data.kind} · {selectedNode.data.id}</p>
              </div>
              <div className="space-y-2">
                {Object.entries(selectedNode.data.details ?? {}).map(([key, value]) => (
                  <div key={key} className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
                    <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-slate-500">{key.replace(/_/g, ' ')}</p>
                    <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-slate-200">
                      {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex h-full min-h-[220px] items-center justify-center text-center text-sm text-slate-400">
              Click a workflow node to inspect its extracted parameters and supporting data.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
