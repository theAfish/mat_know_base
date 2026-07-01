import { useEffect, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  applyNodeChanges,
  type Edge,
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

const XML_NS = 'http://www.w3.org/2000/svg'
const OP_WIDTH = 190
const OBJ_WIDTH = 190
const CONTEXT_WIDTH = 210
const NODE_HEIGHT = 74
const X_GAP = 300
const Y_GAP = 240
const OBJECT_OFFSET = 145
const MIN_ROW_SPACING = 235
const COMPONENT_GAP_X = 360
const COMPONENT_GAP_Y = 150
const BRANCH_GAP = 320

type AnchorSide = 'top' | 'right' | 'bottom' | 'left'
type PositionedNode = WorkflowCanvasNode & { x: number; y: number }

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

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0
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
      if (sourceSide === targetSide) score += 80

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

function connectedComponents(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]) {
  const nodeIds = new Set(nodes.map(node => node.id))
  const adjacency = new Map<string, Set<string>>()
  nodes.forEach(node => adjacency.set(node.id, new Set()))
  edges.forEach(edge => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  })

  const seen = new Set<string>()
  const components: string[][] = []
  nodes.forEach(node => {
    if (seen.has(node.id)) return
    const queue = [node.id]
    const component: string[] = []
    seen.add(node.id)
    while (queue.length) {
      const current = queue.shift()!
      component.push(current)
      adjacency.get(current)?.forEach(next => {
        if (seen.has(next)) return
        seen.add(next)
        queue.push(next)
      })
    }
    components.push(component)
  })
  return components
}

function rowItemsFromAnchors(ids: string[], anchors: Map<string, number>, fallbackGap: number) {
  const rowWidth = (ids.length - 1) * fallbackGap
  return ids.map((id, index) => ({
    id,
    x: anchors.has(id) ? anchors.get(id)! : index * fallbackGap - rowWidth / 2,
  }))
}

function centeredOffsets(count: number, gap: number) {
  const center = (count - 1) / 2
  return Array.from({ length: count }, (_, index) => (index - center) * gap)
}

function workflowLayoutGroups(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]) {
  const nodeMap = new Map(nodes.map(node => [node.id, node]))
  const operationIds = nodes.filter(node => node.kind === 'operation').map(node => node.id)
  if (operationIds.length === 0) return connectedComponents(nodes, edges)

  const producerMap = new Map<string, string[]>()
  const consumerMap = new Map<string, string[]>()
  nodes.filter(node => node.kind === 'object').forEach(node => {
    producerMap.set(node.id, [])
    consumerMap.set(node.id, [])
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

  const opAdjacency = new Map<string, Set<string>>()
  operationIds.forEach(id => opAdjacency.set(id, new Set()))
  producerMap.forEach((producers, objectId) => {
    const consumers = consumerMap.get(objectId) ?? []
    producers.forEach(producerId => {
      consumers.forEach(consumerId => {
        if (producerId === consumerId) return
        opAdjacency.get(producerId)?.add(consumerId)
        opAdjacency.get(consumerId)?.add(producerId)
      })
    })
  })

  const seenOps = new Set<string>()
  const groups: string[][] = []
  const groupByOperation = new Map<string, number>()
  operationIds.forEach(operationId => {
    if (seenOps.has(operationId)) return
    const queue = [operationId]
    const group: string[] = []
    seenOps.add(operationId)
    while (queue.length) {
      const current = queue.shift()!
      groupByOperation.set(current, groups.length)
      group.push(current)
      opAdjacency.get(current)?.forEach(next => {
        if (seenOps.has(next)) return
        seenOps.add(next)
        queue.push(next)
      })
    }
    groups.push(group)
  })

  const ungroupedObjects: string[] = []
  nodes.filter(node => node.kind === 'object').forEach(node => {
    const touchedGroups = new Map<number, number>()
    const touchedOperations = [...(producerMap.get(node.id) ?? []), ...(consumerMap.get(node.id) ?? [])]
    touchedOperations.forEach(operationId => {
      const groupIndex = groupByOperation.get(operationId)
      if (groupIndex === undefined) return
      touchedGroups.set(groupIndex, (touchedGroups.get(groupIndex) ?? 0) + 1)
    })

    if (touchedGroups.size === 0) {
      ungroupedObjects.push(node.id)
      return
    }

    const bestGroup = Array.from(touchedGroups.entries()).sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0]
    groups[bestGroup].push(node.id)
  })

  ungroupedObjects.forEach(objectId => groups.push([objectId]))
  return groups
}

function layoutComponent(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]) {
  const nodeMap = new Map(nodes.map(node => [node.id, node]))
  const operationIds = nodes.filter(node => node.kind === 'operation').map(node => node.id)
  const objectIds = nodes.filter(node => node.kind === 'object').map(node => node.id)
  const contextIds = nodes.filter(node => !['object', 'operation'].includes(node.kind)).map(node => node.id)

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
    queue.sort((a, b) => (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? ''))
  }

  const levels = new Map<number, string[]>()
  operationIds.forEach(id => {
    const level = opLevel.get(id) ?? 0
    if (!levels.has(level)) levels.set(level, [])
    levels.get(level)!.push(id)
  })

  const sortedLevels = Array.from(levels.keys()).sort((a, b) => a - b)
  const levelOrder = new Map<number, string[]>()
  sortedLevels.forEach(level => {
    levelOrder.set(level, [...(levels.get(level) ?? [])].sort((a, b) => (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? '')))
  })

  const opX = new Map<string, number>()
  for (let pass = 0; pass < 6; pass += 1) {
    sortedLevels.forEach(level => {
      const ids = levelOrder.get(level) ?? []
      const anchors = new Map<string, number>()
      ids.forEach(id => {
        const parents = Array.from(opParents.get(id) ?? []).filter(parent => opX.has(parent))
        if (parents.length) anchors.set(id, average(parents.map(parent => opX.get(parent)!)))
      })
      const items = rowItemsFromAnchors(ids, anchors, X_GAP)
      spreadRow(items, MIN_ROW_SPACING)
      items.forEach(item => opX.set(item.id, item.x))
      levelOrder.set(level, items.sort((a, b) => a.x - b.x).map(item => item.id))
    })

    sortedLevels.slice().reverse().forEach(level => {
      const ids = levelOrder.get(level) ?? []
      const anchors = new Map<string, number>()
      ids.forEach(id => {
        const children = Array.from(opChildren.get(id) ?? []).filter(child => opX.has(child))
        if (children.length) anchors.set(id, average(children.map(child => opX.get(child)!)))
      })
      const items = rowItemsFromAnchors(ids, anchors, X_GAP)
      spreadRow(items, MIN_ROW_SPACING)
      items.forEach(item => opX.set(item.id, item.x))
      levelOrder.set(level, items.sort((a, b) => a.x - b.x).map(item => item.id))
    })
  }

  if (operationIds.length === 0) {
    objectIds.forEach((id, index) => opX.set(id, index * MIN_ROW_SPACING))
  }

  const branchOffsets = new Map<string, number[]>()
  const addBranchOffset = (operationId: string, offset: number) => {
    const values = branchOffsets.get(operationId) ?? []
    values.push(offset)
    branchOffsets.set(operationId, values)
  }

  objectIds.forEach(objectId => {
    const consumers = [...(consumerMap.get(objectId) ?? [])].sort((a, b) => {
      const levelDelta = (opLevel.get(a) ?? 0) - (opLevel.get(b) ?? 0)
      if (levelDelta !== 0) return levelDelta
      return (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? '')
    })
    if (consumers.length > 1) {
      centeredOffsets(consumers.length, BRANCH_GAP).forEach((offset, index) => {
        addBranchOffset(consumers[index], offset)
      })
    }

    const producers = [...(producerMap.get(objectId) ?? [])].sort((a, b) => {
      const levelDelta = (opLevel.get(a) ?? 0) - (opLevel.get(b) ?? 0)
      if (levelDelta !== 0) return levelDelta
      return (nodeMap.get(a)?.label ?? '').localeCompare(nodeMap.get(b)?.label ?? '')
    })
    if (producers.length > 1) {
      centeredOffsets(producers.length, BRANCH_GAP).forEach((offset, index) => {
        addBranchOffset(producers[index], offset)
      })
    }
  })

  branchOffsets.forEach((offsets, operationId) => {
    opX.set(operationId, (opX.get(operationId) ?? 0) + average(offsets))
  })

  const levelRows = new Map<number, Array<{ id: string; x: number }>>()
  operationIds.forEach(id => {
    const level = opLevel.get(id) ?? 0
    if (!levelRows.has(level)) levelRows.set(level, [])
    levelRows.get(level)!.push({ id, x: opX.get(id) ?? 0 })
  })
  levelRows.forEach(items => {
    spreadRow(items, MIN_ROW_SPACING)
    items.forEach(item => opX.set(item.id, item.x))
  })

  const objectLayout = objectIds.map(id => {
    const producers = producerMap.get(id) ?? []
    const consumers = consumerMap.get(id) ?? []

    if (producers.length > 0 && consumers.length > 0) {
      const latestLevel = Math.max(...producers.map(producerId => opLevel.get(producerId) ?? 0))
      const earliestLevel = Math.min(...consumers.map(consumerId => opLevel.get(consumerId) ?? 0))
      const nearbyProducers = producers.filter(producerId => (opLevel.get(producerId) ?? 0) === latestLevel)
      const nearbyConsumers = consumers.filter(consumerId => (opLevel.get(consumerId) ?? 0) === earliestLevel)
      return {
        id,
        x: average([...nearbyProducers, ...nearbyConsumers].map(opId => opX.get(opId) ?? 0)),
        y: ((latestLevel + earliestLevel) / 2) * Y_GAP,
      }
    }

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

    const isolatedIndex = objectIds.indexOf(id)
    return { id, x: isolatedIndex * MIN_ROW_SPACING, y: -OBJECT_OFFSET }
  })

  const objectRows = new Map<number, Array<{ id: string; x: number; y: number }>>()
  objectLayout.forEach(item => {
    const rowKey = Math.round(item.y)
    if (!objectRows.has(rowKey)) objectRows.set(rowKey, [])
    objectRows.get(rowKey)!.push(item)
  })
  objectRows.forEach(items => spreadRow(items, MIN_ROW_SPACING))

  const basePositions = new Map<string, PositionedNode>()
  nodes.forEach(node => {
    if (node.kind === 'operation') {
      basePositions.set(node.id, { ...node, x: opX.get(node.id) ?? 0, y: (opLevel.get(node.id) ?? 0) * Y_GAP })
      return
    }
    if (node.kind === 'object') {
      const objectPosition = objectLayout.find(item => item.id === node.id) ?? { x: 0, y: -OBJECT_OFFSET }
      basePositions.set(node.id, { ...node, x: objectPosition.x, y: objectPosition.y })
    }
  })

  const incoming = new Map<string, string[]>()
  const outgoing = new Map<string, string[]>()
  contextIds.forEach(id => {
    incoming.set(id, [])
    outgoing.set(id, [])
  })
  edges.forEach(edge => {
    if (contextIds.includes(edge.source)) outgoing.get(edge.source)?.push(edge.target)
    if (contextIds.includes(edge.target)) incoming.get(edge.target)?.push(edge.source)
  })

  const contextLayout = contextIds.map((id, index) => {
    const downstream = (outgoing.get(id) ?? []).map(targetId => basePositions.get(targetId)).filter(Boolean) as PositionedNode[]
    const upstream = (incoming.get(id) ?? []).map(sourceId => basePositions.get(sourceId)).filter(Boolean) as PositionedNode[]
    const anchors = downstream.length ? downstream : upstream
    if (anchors.length) {
      return {
        id,
        x: average(anchors.map(node => node.x)),
        y: average(anchors.map(node => node.y)) - OBJECT_OFFSET,
      }
    }
    return { id, x: index * MIN_ROW_SPACING, y: -OBJECT_OFFSET * 2 }
  })
  const contextRows = new Map<number, Array<{ id: string; x: number; y: number }>>()
  contextLayout.forEach(item => {
    const rowKey = Math.round(item.y)
    if (!contextRows.has(rowKey)) contextRows.set(rowKey, [])
    contextRows.get(rowKey)!.push(item)
  })
  contextRows.forEach(items => spreadRow(items, MIN_ROW_SPACING))
  contextLayout.forEach(item => {
    const node = nodeMap.get(item.id)
    if (node) basePositions.set(item.id, { ...node, x: item.x, y: item.y })
  })

  const positioned: PositionedNode[] = nodes.map(node => basePositions.get(node.id) ?? { ...node, x: 0, y: 0 })

  return positioned
}

function packComponents(components: PositionedNode[][]) {
  const packed: PositionedNode[] = []
  let cursorX = 0
  let cursorY = 0
  let rowHeight = 0
  const maxRowWidth = Math.max(1200, Math.ceil(Math.sqrt(components.length || 1)) * 900)

  const sorted = [...components].sort((a, b) => b.length - a.length)
  sorted.forEach(component => {
    const bounds = component.reduce(
      (acc, node) => {
        const width = nodeWidth(node.kind)
        return {
          minX: Math.min(acc.minX, node.x),
          minY: Math.min(acc.minY, node.y),
          maxX: Math.max(acc.maxX, node.x + width),
          maxY: Math.max(acc.maxY, node.y + NODE_HEIGHT),
        }
      },
      { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY, maxY: Number.NEGATIVE_INFINITY },
    )
    const width = bounds.maxX - bounds.minX
    const height = bounds.maxY - bounds.minY
    if (cursorX > 0 && cursorX + width > maxRowWidth) {
      cursorX = 0
      cursorY += rowHeight + COMPONENT_GAP_Y
      rowHeight = 0
    }

    component.forEach(node => {
      packed.push({
        ...node,
        x: node.x - bounds.minX + cursorX,
        y: node.y - bounds.minY + cursorY,
      })
    })

    cursorX += width + COMPONENT_GAP_X
    rowHeight = Math.max(rowHeight, height)
  })

  const bounds = packed.reduce(
    (acc, node) => ({
      minX: Math.min(acc.minX, node.x),
      maxX: Math.max(acc.maxX, node.x + nodeWidth(node.kind)),
    }),
    { minX: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY },
  )
  const centerShift = (bounds.minX + bounds.maxX) / 2
  return packed.map(node => ({ ...node, x: node.x - centerShift }))
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
    }
  })
}

function buildLayout(nodes: WorkflowCanvasNode[], edges: WorkflowCanvasEdge[]): { nodes: Node<WorkflowNodeData>[]; edges: Edge[] } {
  if (nodes.length === 0) return { nodes: [], edges: [] }

  const nodeMap = new Map(nodes.map(node => [node.id, node]))
  const validEdges = edges.filter(edge => nodeMap.has(edge.source) && nodeMap.has(edge.target))
  const components = workflowLayoutGroups(nodes, validEdges).map(componentIds => {
    const idSet = new Set(componentIds)
    const componentNodes = nodes.filter(node => idSet.has(node.id))
    const componentEdges = validEdges.filter(edge => idSet.has(edge.source) && idSet.has(edge.target))
    return layoutComponent(componentNodes, componentEdges)
  })
  const packedNodes = packComponents(components)
  const flowNodes: Node<WorkflowNodeData>[] = packedNodes.map(node => ({
    id: node.id,
    type: node.kind,
    position: { x: node.x, y: node.y },
    data: { id: node.id, kind: node.kind, label: node.label, title: node.title, details: node.details },
  }))

  const flowEdges: Edge[] = validEdges.map(edge => {
    return {
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
      setFlowEdges(currentEdges => anchorEdges(currentEdges, nextNodes))
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
