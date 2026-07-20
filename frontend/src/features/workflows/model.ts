export type WorkflowNodeKind = 'object' | 'operation' | 'planning' | 'reasoning' | 'unknown'
export interface WorkflowCanvasNode { id: string; label: string; kind: WorkflowNodeKind; title?: string; details?: Record<string, unknown> }
export interface WorkflowCanvasEdge { id: string; source: string; target: string; label?: string; title?: string }

export const escapeXml = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;')
export function labelLines(label: string, maxChars = 18, maxLines = 3): string[] {
  const words = label.split(/\s+/).filter(Boolean)
  if (!words.length) return ['']
  const lines: string[] = []; let current = ''; let index = 0
  while (index < words.length && lines.length < maxLines) {
    const candidate = current ? `${current} ${words[index]}` : words[index]
    if (candidate.length <= maxChars || !current) { current = candidate; index += 1 }
    else { lines.push(current); current = '' }
  }
  if (current && lines.length < maxLines) lines.push(current)
  if (index < words.length) lines[lines.length - 1] = `${lines[lines.length - 1].replace(/…$/, '')}…`
  return lines
}
