export type ProjectionTableRow = Record<string, string>
export type ColumnDataType = 'boolean' | 'number' | 'date' | 'text'

const PAGE_STORAGE_PREFIX = 'mkb:projection-table-page:'

export const isBlank = (value: unknown) => String(value ?? '').trim() === ''
export const parseBoolean = (value: unknown): number | null => {
  const normalized = String(value ?? '').trim().toLowerCase()
  if (['true', 'yes', 'y', '1'].includes(normalized)) return 1
  if (['false', 'no', 'n', '0'].includes(normalized)) return 0
  return null
}
export const parseNumber = (value: unknown): number | null => {
  const normalized = String(value ?? '').trim().replace(/,/g, '')
  if (!normalized) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}
export const parseDate = (value: unknown): number | null => {
  const parsed = Date.parse(String(value ?? '').trim())
  return Number.isNaN(parsed) ? null : parsed
}
export function inferColumnDataType(rows: ProjectionTableRow[], column: string): ColumnDataType {
  const values = rows.map(row => row[column]).filter(value => !isBlank(value))
  if (!values.length) return 'text'
  if (values.every(value => parseBoolean(value) !== null)) return 'boolean'
  if (values.every(value => parseNumber(value) !== null)) return 'number'
  if (values.every(value => parseDate(value) !== null)) return 'date'
  return 'text'
}
export const compareText = (left: unknown, right: unknown) => String(left ?? '').localeCompare(
  String(right ?? ''), undefined, { numeric: true, sensitivity: 'base' },
)
export function sortLabel(type: ColumnDataType, sorted: false | 'asc' | 'desc'): string {
  if (!sorted) return 'Click to sort'
  if (type === 'boolean') return sorted === 'asc' ? 'False → True (click for True → False)' : 'True → False (click to clear)'
  if (type === 'number') return sorted === 'asc' ? '0 → 9 (click for 9 → 0)' : '9 → 0 (click to clear)'
  if (type === 'date') return sorted === 'asc' ? 'Old → New (click for New → Old)' : 'New → Old (click to clear)'
  return sorted === 'asc' ? 'A → Z (click for Z → A)' : 'Z → A (click to clear)'
}
export const sortArrow = (sorted: false | 'asc' | 'desc') => !sorted ? '⇅' : sorted === 'asc' ? '↑' : '↓'
export function loadSavedPage(key: string): number {
  if (typeof window === 'undefined') return 1
  const page = Number(window.sessionStorage.getItem(`${PAGE_STORAGE_PREFIX}${key}`) || 1)
  return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1
}
export function savePage(key: string, page: number): void {
  if (typeof window !== 'undefined') window.sessionStorage.setItem(`${PAGE_STORAGE_PREFIX}${key}`, String(page))
}
