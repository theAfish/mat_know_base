import type { Project, Projection } from '../../types'


export const GLOBAL_KG_SPACE = '__global_kg__'
export const PAGE_SIZE = 50


export function paperName(project: Project | undefined): string {
  if (!project) return ''
  if (project.label) return project.label
  const src = project.source_path ?? ''
  if (src) {
    const parts = src.replace(/[/\\]+$/, '').split(/[/\\]/)
    const folder = parts[parts.length - 1]
    if (folder) return folder
  }
  return project.project_id.slice(0, 12)
}

export function stringify(val: unknown): string {
  if (val === null || val === undefined) return ''
  if (typeof val === 'string') return val
  if (typeof val === 'boolean' || typeof val === 'number') return String(val)
  if (Array.isArray(val)) return val.map(stringify).join(', ')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

export function defaultColumns(cols: string[], schemaOrder?: string[]): string[] {
  const idCols = new Set(cols.filter(c =>
    c.toLowerCase() === 'id' || c.toLowerCase().endsWith('_id') || c.toLowerCase().includes('_id_'),
  ))
  const metaCols = ['paper_name', 'source_paper_name', 'is_core_study_data', 'extracted_at']
    .filter(c => cols.includes(c))
  if (schemaOrder && schemaOrder.length > 0) {
    const schemaPresent = schemaOrder.filter(c => cols.includes(c))
    const rest = cols.filter(c => !schemaPresent.includes(c) && !metaCols.includes(c) && !idCols.has(c))
    const visible = [...schemaPresent, ...metaCols, ...rest]
    return visible.length > 0 ? visible : cols
  }
  const rest = cols.filter(c => !metaCols.includes(c) && !idCols.has(c))
  const visible = [...metaCols, ...rest]
  return visible.length > 0 ? visible : cols
}

export function buildSectionRows(
  projections: Projection[],
  paperLookup: Record<string, string>,
): Record<string, Array<Record<string, string>>> {
  const sectionRows: Record<string, Array<Record<string, string>>> = {}
  for (const proj of projections) {
    if (!['COMPLETED', 'REVIEWED'].includes(proj.status) || !proj.data) continue
    const pname = paperLookup[proj.project_id] ?? ''
    const meta = {
      project_id: proj.project_id,
      projection_id: proj.projection_id,
      extracted_at: (proj.extracted_at ?? '').slice(0, 10),
      ...(pname ? { paper_name: pname } : {}),
    }
    for (const [section, value] of Object.entries(proj.data)) {
      const rows = sectionToRows(value, meta, paperLookup)
      if (rows.length > 0) {
        sectionRows[section] = [...(sectionRows[section] ?? []), ...rows]
      }
    }
  }
  return sectionRows
}

export function sectionToRows(
  value: unknown,
  meta: Record<string, string>,
  paperLookup: Record<string, string>,
): Array<Record<string, string>> {
  if (Array.isArray(value)) {
    if (value.length === 0) return []
    if (value.every(v => typeof v === 'object' && v !== null && !Array.isArray(v))) {
      const rows = (value as Record<string, unknown>[]).map(item => {
        const row: Record<string, string> = { ...meta }
        for (const [k, v] of Object.entries(item)) row[k] = stringify(v)
        if (row.source_project_id && paperLookup[row.source_project_id]) {
          row.source_paper_name = row.source_paper_name ?? paperLookup[row.source_project_id]
        }
        return row
      })
      return rows.sort((a, b) => {
        const av = String(a.is_core_study_data ?? '').toLowerCase()
        const bv = String(b.is_core_study_data ?? '').toLowerCase()
        const aCore = ['true', '1', 'yes'].includes(av)
        const bCore = ['true', '1', 'yes'].includes(bv)
        return (bCore ? 1 : 0) - (aCore ? 1 : 0)
      })
    }
    return value.map(v => ({ ...meta, value: stringify(v) }))
  }
  if (typeof value === 'object' && value !== null) {
    return [{ ...meta, ...Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, stringify(v)])) }]
  }
  return [{ ...meta, value: stringify(value) }]
}


export type ColPrefs = {
  visible: string[]
  widths: Record<string, number>
  known: string[]
}

const COL_PREFS_KEY = (name: string) => `mkb_proj_cols::${name}`

export function loadColPrefs(name: string): ColPrefs | null {
  try {
    const raw = localStorage.getItem(COL_PREFS_KEY(name))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || !Array.isArray(parsed.visible)) return null
    return {
      visible: parsed.visible,
      widths: parsed.widths ?? {},
      known: Array.isArray(parsed.known) ? parsed.known : parsed.visible,
    }
  } catch {
    return null
  }
}

export function saveColPrefs(name: string, prefs: ColPrefs) {
  try { localStorage.setItem(COL_PREFS_KEY(name), JSON.stringify(prefs)) } catch { /* ignore quota */ }
}

function escapeCsvCell(value: string): string {
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`
  return value
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export function slugifyExportName(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return normalized || 'projection_table'
}

export function exportTableRows(
  rows: Array<Record<string, string>>,
  columns: string[],
  baseName: string,
  format: 'csv' | 'excel',
) {
  const safeBaseName = slugifyExportName(baseName)
  if (format === 'csv') {
    const csv = [
      columns.map(escapeCsvCell).join(','),
      ...rows.map(row => columns.map(col => escapeCsvCell(row[col] ?? '')).join(',')),
    ].join('\r\n')
    triggerDownload(
      new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8;' }),
      `${safeBaseName}.csv`,
    )
    return
  }

  const sheetName = escapeXml(baseName.slice(0, 31) || 'Sheet1')
  const headerCells = columns
    .map(col => `<Cell><Data ss:Type="String">${escapeXml(col)}</Data></Cell>`)
    .join('')
  const bodyRows = rows
    .map(row => (
      `<Row>${columns.map(col => (
        `<Cell><Data ss:Type="String">${escapeXml(row[col] ?? '')}</Data></Cell>`
      )).join('')}</Row>`
    ))
    .join('')
  const workbook = `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="${sheetName}">
  <Table>
   <Row>${headerCells}</Row>
   ${bodyRows}
  </Table>
 </Worksheet>
</Workbook>`
  triggerDownload(
    new Blob([workbook], { type: 'application/vnd.ms-excel;charset=utf-8;' }),
    `${safeBaseName}.xls`,
  )
}
