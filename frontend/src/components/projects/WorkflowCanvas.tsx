import { useEffect, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from 'reactflow'
import 'reactflow/dist/style.css'

type WorkflowNodeKind = 'object' | 'operation'

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

const XML_NS = 'http://www.w3.org/2000/svg'
const OP_WIDTH = 190
const OBJ_WIDTH = 190
const NODE_HEIGHT = 74
const X_GAP = 260
const Y_GAP = 240
const OBJECT_OFFSET = 145
const MIN_ROW_SPACING = 235

function NodeHandles() {
  return (
    <>
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <Handle type="target" position={Position.Left} className="opacity-0" />
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
      <Handle type="source" position={Position.Right} className="opacity-0" />
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

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0
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
  return kind === 'operation' ? OP_WIDTH : OBJ_WIDTH
}

function nodeBounds(node: Node<WorkflowNodeData>) {
  const width = nodeWidth(node.type === 'operation' ? 'operation' : 'object')
  return {
    x: node.position.x,
    y: node.position.y,
    width,
    height: NODE_HEIGHT,
    centerX: node.position.x + width / 2,
    centerY: node.position.y + NODE_HEIGHT / 2,
  }
}

function edgePath(source: Node<WorkflowNodeData>, target: Node<WorkflowNodeData>) {
  const sb = nodeBounds(source)
  const tb = nodeBounds(target)
  const sourceBelow = tb.centerY >= sb.centerY
  const startX = sb.centerX
  const startY = sourceBelow ? sb.y + sb.height : sb.y
  const endX = tb.centerX
  const endY = sourceBelow ? tb.y : tb.y + tb.height
  const midY = startY + (endY - startY) / 2
  return `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`
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

function spreadRow<T extends { x: number }>(items: T[], minSpacing: number) {
  if (items.length <= 1) return
  items.sort((a, b) => a.x - b.x)
  for (let index = 1; index < items.length; index += 1) {
    if (items[index].x - items[index - 1].x < minSpacing) {
      items[index].x = items[index - 1].x + minSpacing
    }
  }

  const midpoint = (items[0].x + items[items.length - 1].x) / 2
  const targetCenter = average(items.map(item => item.x))
  const shift = midpoint - targetCenter
  items.forEach(item => {
    item.x -= shift
  })
}

function buildLayout(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]): { nodes: Node<WorkflowNodeData>[]; edges: Edge[] } {
  const nodeMap = new Map(nodes.map(node => [node.id, node]))
  const operationIds = nodes.filter(node => node.kind === 'operation').map(node => node.id)
  const objectIds = nodes.filter(node => node.kind === 'object').map(node => node.id)

  const producerMap = new Map<string, string[]>()
  const consumerMap = new Map<string, string[]>()
  objectIds.forEach(id => {
    producerMap.set(id, [])
    consumerMap.set(id, [])
  })

  edges.forEach(edge => {
    const sourceKind = nodeMap.get(edge.source)?.kind
    const targetKind = nodeMap.get(edge.target)?.kind
    if (sourceKind === 'operation' && targetKind === 'object') {
      producerMap.get(edge.target)?.push(edge.source)
    }
    if (sourceKind === 'object' && targetKind === 'operation') {
      consumerMap.get(edge.source)?.push(edge.target)
    }
  })

  const opChildren = new Map<string, Set<string>>()
  const opParents = new Map<string, Set<string>>()
  const indegree = new Map<string, number>()
  operationIds.forEach(id => {
    opChildren.set(id, new Set())
    opParents.set(id, new Set())
    indegree.set(id, 0)
  })

  objectIds.forEach(objectId => {
    const producers = producerMap.get(objectId) ?? []
    const consumers = consumerMap.get(objectId) ?? []
    producers.forEach(producerId => {
      consumers.forEach(consumerId => {
        if (producerId === consumerId || opChildren.get(producerId)?.has(consumerId)) return
        opChildren.get(producerId)?.add(consumerId)
        opParents.get(consumerId)?.add(producerId)
        indegree.set(consumerId, (indegree.get(consumerId) ?? 0) + 1)
      })
    })
  })

  const queue = operationIds
    .filter(id => (indegree.get(id) ?? 0) === 0)
    .sort((a, b) => (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? ''))

  const opLevel = new Map<string, number>()
  operationIds.forEach(id => opLevel.set(id, 0))

  while (queue.length > 0) {
    const currentId = queue.shift()!
    const currentLevel = opLevel.get(currentId) ?? 0
    Array.from(opChildren.get(currentId) ?? []).forEach(childId => {
      opLevel.set(childId, Math.max(opLevel.get(childId) ?? 0, currentLevel + 1))
      indegree.set(childId, (indegree.get(childId) ?? 1) - 1)
      if ((indegree.get(childId) ?? 0) === 0) {
        queue.push(childId)
      }
    })
  }

  const levels = new Map<number, string[]>()
  operationIds.forEach(id => {
    const level = opLevel.get(id) ?? 0
    if (!levels.has(level)) levels.set(level, [])
    levels.get(level)!.push(id)
  })

  const opX = new Map<string, number>()
  Array.from(levels.keys()).sort((a, b) => a - b).forEach(level => {
    const ids = levels.get(level) ?? []
    ids.sort((a, b) => {
      const aParents = Array.from(opParents.get(a) ?? [])
      const bParents = Array.from(opParents.get(b) ?? [])
      const aAnchor = aParents.length ? average(aParents.map(parentId => opX.get(parentId) ?? 0)) : 0
      const bAnchor = bParents.length ? average(bParents.map(parentId => opX.get(parentId) ?? 0)) : 0
      if (aAnchor !== bAnchor) return aAnchor - bAnchor
      return (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? '')
    })

    const rowWidth = (ids.length - 1) * X_GAP
    ids.forEach((id, index) => {
      opX.set(id, index * X_GAP - rowWidth / 2)
    })

    const rowItems = ids.map(id => ({ id, x: opX.get(id) ?? 0 }))
    spreadRow(rowItems, MIN_ROW_SPACING)
    rowItems.forEach(item => {
      opX.set(item.id, item.x)
    })
  })

  const objectLayout = objectIds.map(id => {
    const producers = producerMap.get(id) ?? []
    const consumers = consumerMap.get(id) ?? []

    if (consumers.length > 0) {
      const earliestLevel = Math.min(...consumers.map(consumerId => opLevel.get(consumerId) ?? 0))
      const earliestConsumers = consumers.filter(consumerId => (opLevel.get(consumerId) ?? 0) === earliestLevel)
      return {
        id,
        x: average(earliestConsumers.map(consumerId => opX.get(consumerId) ?? 0)),
        y: earliestLevel * Y_GAP - OBJECT_OFFSET,
      }
    }

    if (producers.length > 0) {
      const latestLevel = Math.max(...producers.map(producerId => opLevel.get(producerId) ?? 0))
      const latestProducers = producers.filter(producerId => (opLevel.get(producerId) ?? 0) === latestLevel)
      return {
        id,
        x: average(latestProducers.map(producerId => opX.get(producerId) ?? 0)),
        y: latestLevel * Y_GAP + OBJECT_OFFSET,
      }
    }

    return { id, x: 0, y: -OBJECT_OFFSET }
  })

  const objectRows = new Map<number, Array<{ id: string; x: number; y: number }>>()
  objectLayout.forEach(item => {
    const rowKey = Math.round(item.y)
    if (!objectRows.has(rowKey)) objectRows.set(rowKey, [])
    objectRows.get(rowKey)!.push(item)
  })

  objectRows.forEach(items => {
    spreadRow(items, MIN_ROW_SPACING)
  })

  const flowNodes: Node<WorkflowNodeData>[] = nodes.map(node => {
    if (node.kind === 'operation') {
      return {
        id: node.id,
        type: 'operation',
        position: { x: opX.get(node.id) ?? 0, y: (opLevel.get(node.id) ?? 0) * Y_GAP },
        data: { id: node.id, kind: node.kind, label: node.label, title: node.title, details: node.details },
      }
    }

    const layout = objectLayout.find(item => item.id === node.id) ?? { x: 0, y: -OBJECT_OFFSET }
    return {
      id: node.id,
      type: 'object',
      position: { x: layout.x, y: layout.y },
      data: { id: node.id, kind: node.kind, label: node.label, title: node.title, details: node.details },
    }
  })

  const flowEdges: Edge[] = edges.map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    label: edge.label,
    data: edge.title ? { title: edge.title } : undefined,
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#64748b' },
    style: { stroke: '#64748b', strokeWidth: 1.5 },
    labelStyle: { fill: '#94a3b8', fontSize: 11 },
    labelBgStyle: { fill: 'rgba(9, 9, 11, 0.92)', fillOpacity: 1 },
    labelBgPadding: [6, 2],
    labelBgBorderRadius: 6,
  }))

  return { nodes: flowNodes, edges: flowEdges }
}

const nodeTypes = {
  operation: OperationNode,
  object: ObjectNode,
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
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<WorkflowNodeData>([])
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  useEffect(() => {
    const layout = buildLayout(nodes, edges)
    setFlowNodes(layout.nodes)
    setFlowEdges(layout.edges)
    setSelectedNodeId(null)
  }, [edges, nodes, setFlowEdges, setFlowNodes])

  const selectedNode = flowNodes.find(node => node.id === selectedNodeId) ?? null

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
      if (node.type === 'operation') {
        svgParts.push(
          `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="16" ry="16" fill="#4c1d95" fill-opacity="0.88" stroke="#a78bfa" stroke-width="1.5" />`,
        )
      } else {
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
        const kind = node.type === 'operation' ? 'operation' : 'object'
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
            onNodesChange={onNodesChange}
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
              nodeColor={node => node.type === 'operation' ? '#8b5cf6' : '#14b8a6'}
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
