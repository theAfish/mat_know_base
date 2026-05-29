import type { Job, UploadExpandFile } from '../../types'

export type UploadStep =
  | 'idle'
  | 'uploading'   // streaming files to server temp
  | 'expanding'   // extracting archives + listing tree
  | 'reviewing'   // user assigns files to projects
  | 'ingesting'   // backend job running
  | 'done'
  | 'error'

export type GroupingMode = 'top' | 'leaf' | 'single' | 'depth'

export interface ReviewFile {
  id: string                    // = uploadPath (unique)
  uploadPath: string
  segments: string[]
  name: string                  // basename
  size: number
  projectId: string | null      // null = excluded
  relativePath: string          // within the assigned project
}

export interface ReviewProject {
  id: string
  name: string
  /** True once the user manually edits the name in the preview. */
  nameEdited?: boolean
}

export interface UploadState {
  step: UploadStep
  uploadId: string | null
  progress: string
  uploadJob: Job | null
  error: string | null
  files: ReviewFile[]
  projects: ReviewProject[]
  grouping: GroupingMode
  depthN: number
  extracted: Array<{ archive: string; count: number }>
  failed: Array<{ archive: string; error: string }>
}

export const INITIAL_UPLOAD_STATE: UploadState = {
  step: 'idle',
  uploadId: null,
  progress: '',
  uploadJob: null,
  error: null,
  files: [],
  projects: [],
  grouping: 'top',
  depthN: 1,
  extracted: [],
  failed: [],
}

export function slug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

/** Strip the last file extension (e.g. "paper.pdf" → "paper"). */
export function stemName(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(0, dot) : name
}

export const ARCHIVE_SUFFIXES = [
  '.tar.gz', '.tar.bz2', '.tar.xz',
  '.tgz', '.tbz2', '.tbz', '.txz',
  '.tar', '.zip',
]

export function stripArchiveExt(name: string): string {
  const lower = name.toLowerCase()
  for (const ext of ARCHIVE_SUFFIXES) {
    if (lower.endsWith(ext)) return name.slice(0, -ext.length)
  }
  return name
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** Recursively collect all File objects from a DataTransferItem entry. */
export async function collectFromEntry(
  entry: FileSystemEntry,
  prefix: string,
): Promise<Array<{ file: File; relativePath: string }>> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry
    return new Promise(resolve => {
      fileEntry.file(f => resolve([{ file: f, relativePath: prefix ? `${prefix}/${f.name}` : f.name }]))
    })
  }
  if (entry.isDirectory) {
    const dirEntry = entry as FileSystemDirectoryEntry
    const reader = dirEntry.createReader()
    const allEntries: FileSystemEntry[] = []
    await new Promise<void>(resolve => {
      const read = () => {
        reader.readEntries(entries => {
          if (entries.length === 0) return resolve()
          allEntries.push(...entries)
          read()
        })
      }
      read()
    })
    const results = await Promise.all(
      allEntries.map(e => collectFromEntry(e, prefix ? `${prefix}/${e.name}` : e.name))
    )
    return results.flat()
  }
  return []
}

/** Compute project key + relativePath for a file under a grouping mode. */
export function deriveGrouping(
  segments: string[],
  mode: GroupingMode,
  depthN: number,
): { projectId: string; relativePath: string } {
  const filename = segments[segments.length - 1] || 'file'
  if (mode === 'single') {
    return { projectId: '__all__', relativePath: segments.join('/') }
  }
  if (mode === 'leaf') {
    const parent = segments.slice(0, -1).join('/')
    return { projectId: parent || '__root__', relativePath: filename }
  }
  if (mode === 'depth') {
    const n = Math.max(0, depthN)
    if (n === 0) {
      return { projectId: segments.join('/'), relativePath: filename }
    }
    const rootLen = Math.min(n, segments.length - 1)
    const rootSegs = segments.slice(0, rootLen)
    return {
      projectId: rootSegs.join('/') || '__root__',
      relativePath: segments.slice(rootLen).join('/') || filename,
    }
  }
  // 'top'
  const rootLen = Math.min(1, segments.length - 1)
  const rootSegs = segments.slice(0, rootLen)
  return {
    projectId: rootSegs.join('/') || '__root__',
    relativePath: segments.slice(rootLen).join('/') || filename,
  }
}

export function defaultProjectName(projectId: string): string {
  if (projectId === '__all__') return 'project'
  if (projectId === '__root__') return 'project'
  const last = projectId.split('/').pop() || 'project'
  const stripped = stripArchiveExt(last)
  const base = stripped !== last ? stripped : stemName(stripped)
  return slug(base) || 'project'
}

/** Apply a grouping mode to a flat file list, producing fresh project buckets. */
export function applyGrouping(
  files: UploadExpandFile[],
  mode: GroupingMode,
  depthN: number,
): { files: ReviewFile[]; projects: ReviewProject[] } {
  const seen = new Map<string, ReviewProject>()
  const out: ReviewFile[] = []
  for (const f of files) {
    const segments = f.uploadPath.split('/').filter(Boolean)
    const { projectId, relativePath } = deriveGrouping(segments, mode, depthN)
    if (!seen.has(projectId)) {
      seen.set(projectId, { id: projectId, name: defaultProjectName(projectId) })
    }
    out.push({
      id: f.uploadPath,
      uploadPath: f.uploadPath,
      segments,
      name: segments[segments.length - 1] || f.uploadPath,
      size: f.size,
      projectId,
      relativePath: relativePath || (segments[segments.length - 1] || 'file'),
    })
  }
  const counts = new Map<string, number>()
  const projects: ReviewProject[] = []
  for (const p of seen.values()) {
    const c = counts.get(p.name) ?? 0
    counts.set(p.name, c + 1)
    projects.push(c === 0 ? p : { ...p, name: `${p.name}_${c + 1}` })
  }
  projects.sort((a, b) => a.name.localeCompare(b.name))
  return { files: out, projects }
}
