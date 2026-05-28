import { nextJobPollDelayMs, shouldStopJobPolling } from '../api/jobPolling'
import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import {
  listProjects, processProject, extractProject, projectToSpace, kgExtractProject, getProjectJobs,
  listAssets, listProcessedAssets,
} from '../api/projects'
import { listSpaces } from '../api/spaces'
import { uploadInit, uploadFile, uploadComplete, uploadExpand, uploadIngest, uploadProcessedAsset } from '../api/upload'
import { getJob, cancelJob } from '../api/jobs'
import StatusBadge from '../components/StatusBadge'
import JobProgress from '../components/JobProgress'
import ProjectGroupedList from '../components/ProjectGroupedList'
import type {
  Project, Space, Job, UploadProject, UploadFileEntry, Asset, ProcessedAsset,
  UploadExpandFile,
} from '../types'

// ─── helpers ─────────────────────────────────────────────────────────────────

function slug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

const ARCHIVE_SUFFIXES = [
  '.tar.gz', '.tar.bz2', '.tar.xz',
  '.tgz', '.tbz2', '.tbz', '.txz',
  '.tar', '.zip',
]

function stripArchiveExt(name: string): string {
  const lower = name.toLowerCase()
  for (const ext of ARCHIVE_SUFFIXES) {
    if (lower.endsWith(ext)) return name.slice(0, -ext.length)
  }
  return name
}

/** Recursively collect all File objects from a DataTransferItem entry. */
async function collectFromEntry(
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
    // readEntries may return batches; keep reading until done
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

/** Turn dropped DataTransfer items into UploadProject list, ready for the server */
async function buildProjectList(
  items: DataTransferItemList,
): Promise<Array<{ name: string; files: Array<{ file: File; relativePath: string }> }>> {
  const topEntries: Array<{ entry: FileSystemEntry; name: string }> = []
  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry?.()
    if (entry) topEntries.push({ entry, name: entry.name })
  }

  const projects = await Promise.all(
    topEntries.map(async ({ entry, name }) => {
      const files = await collectFromEntry(entry, '')
      return { name, files }
    })
  )
  return projects.filter(p => p.files.length > 0)
}

// ─── Upload tab ───────────────────────────────────────────────────────────────

type UploadStep =
  | 'idle'
  | 'uploading'   // streaming files to server temp
  | 'expanding'   // extracting archives + listing tree
  | 'reviewing'   // user assigns files to projects
  | 'ingesting'   // backend job running
  | 'done'
  | 'error'

type GroupingMode = 'top' | 'leaf' | 'single' | 'depth'

interface ReviewFile {
  id: string                    // = uploadPath (unique)
  uploadPath: string
  segments: string[]
  name: string                  // basename
  size: number
  projectId: string | null      // null = excluded
  relativePath: string          // within the assigned project
}

interface ReviewProject {
  id: string
  name: string
  /** True once the user manually edits the name in the preview. */
  nameEdited?: boolean
}

interface UploadState {
  step: UploadStep
  uploadId: string | null
  progress: string
  uploadJob: Job | null
  error: string | null
  // review-step state:
  files: ReviewFile[]
  projects: ReviewProject[]
  grouping: GroupingMode
  depthN: number
  extracted: Array<{ archive: string; count: number }>
  failed: Array<{ archive: string; error: string }>
}

const INITIAL_UPLOAD_STATE: UploadState = {
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

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** Compute project key + relativePath for a file under a grouping mode. */
function deriveGrouping(
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
    const n = Math.max(1, depthN)
    // root segments = first n folder segments (capped so filename remains)
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

function defaultProjectName(projectId: string): string {
  if (projectId === '__all__') return 'project'
  if (projectId === '__root__') return 'project'
  const last = projectId.split('/').pop() || 'project'
  return slug(stripArchiveExt(last)) || 'project'
}

/** Apply a grouping mode to a flat file list, producing fresh project buckets. */
function applyGrouping(
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
  // Dedupe project names (when two distinct ids derive same default name)
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

function UploadTab() {
  const [state, setState] = useState<UploadState>(INITIAL_UPLOAD_STATE)
  const dropRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) clearTimeout(pollRef.current)
  }

  const pollJob = useCallback((jobId: string) => {
    let consecutiveErrors = 0
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        consecutiveErrors = 0
        setState(s => ({ ...s, uploadJob: job }))
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          pollRef.current = setTimeout(poll, 1000)
        } else if (job.status === 'COMPLETED') {
          setState(s => ({ ...s, step: 'done', uploadJob: job }))
        } else {
          setState(s => ({
            ...s,
            step: 'error',
            error: job.error ?? 'Ingest failed',
            uploadJob: job,
          }))
        }
      } catch (error) {
        consecutiveErrors += 1
        if (shouldStopJobPolling(error, consecutiveErrors)) {
          setState(s => ({
            ...s,
            step: 'error',
            error: 'Upload status is no longer available. Please refresh and retry if needed.',
          }))
          return
        }
        pollRef.current = setTimeout(poll, nextJobPollDelayMs(consecutiveErrors))
      }
    }
    pollRef.current = setTimeout(poll, 1000)
  }, [])

  useEffect(() => stopPoll, [])

  /** Upload dropped items to temp, expand archives, then go to review. */
  const handleDroppedItems = useCallback(async (items: DataTransferItemList) => {
    // Collect every dropped entry as a flat list, prefixed by its top-level name
    // so we preserve user-visible structure on the server.
    const topEntries: Array<{ entry: FileSystemEntry; name: string }> = []
    for (let i = 0; i < items.length; i++) {
      const entry = items[i].webkitGetAsEntry?.()
      if (entry) topEntries.push({ entry, name: entry.name })
    }
    if (topEntries.length === 0) return

    const all: Array<{ file: File; relativePath: string }> = []
    for (const { entry, name } of topEntries) {
      const collected = await collectFromEntry(entry, name)
      all.push(...collected)
    }
    if (all.length === 0) return

    setState({ ...INITIAL_UPLOAD_STATE, step: 'uploading', progress: 'Initializing upload…' })

    try {
      const { upload_id } = await uploadInit()
      for (let i = 0; i < all.length; i++) {
        const { file, relativePath } = all[i]
        setState(s => ({
          ...s,
          uploadId: upload_id,
          progress: `Uploading ${i + 1}/${all.length}: ${file.name}`,
        }))
        let uploaded = false
        let lastErr: unknown
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            await uploadFile(upload_id, file, relativePath, relativePath)
            uploaded = true
            break
          } catch (err) {
            lastErr = err
            if (attempt < 2) await new Promise(r => setTimeout(r, 1500 * (attempt + 1)))
          }
        }
        if (!uploaded) throw lastErr
      }
      await uploadComplete(upload_id)

      setState(s => ({ ...s, step: 'expanding', progress: 'Extracting archives…' }))
      const expanded = await uploadExpand(upload_id)

      const { files, projects } = applyGrouping(expanded.files, 'top', 1)
      setState(s => ({
        ...s,
        step: 'reviewing',
        uploadId: upload_id,
        files,
        projects,
        grouping: 'top',
        depthN: 1,
        extracted: expanded.extracted,
        failed: expanded.failed,
        progress: '',
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, step: 'error', error: msg }))
    }
  }, [])

  const onDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    await handleDroppedItems(e.dataTransfer.items)
  }, [handleDroppedItems])

  /** Re-apply a grouping preset, discarding manual edits. */
  const reapplyGrouping = (mode: GroupingMode, depthN: number) => {
    setState(s => {
      const flat: UploadExpandFile[] = s.files.map(f => ({ uploadPath: f.uploadPath, size: f.size }))
      const { files, projects } = applyGrouping(flat, mode, depthN)
      return { ...s, files, projects, grouping: mode, depthN }
    })
  }

  const renameProject = (id: string, name: string) => {
    setState(s => ({
      ...s,
      projects: s.projects.map(p =>
        p.id === id ? { ...p, name, nameEdited: true } : p,
      ),
    }))
  }

  const removeProject = (id: string) => {
    // Exclude every file in this project (does not delete the bucket so it can
    // still be reused as a move target if desired — but with 0 files it will
    // be hidden from the UI list).
    setState(s => ({
      ...s,
      files: s.files.map(f => f.projectId === id ? { ...f, projectId: null } : f),
    }))
  }

  const setFileProject = (fileId: string, projectId: string | null) => {
    setState(s => ({
      ...s,
      files: s.files.map(f => {
        if (f.id !== fileId) return f
        // When moving to a different project, drop sub-path context and use
        // just the filename — the file no longer belongs to the original tree.
        return projectId === f.projectId
          ? f
          : { ...f, projectId, relativePath: projectId ? f.name : f.relativePath }
      }),
    }))
  }

  const addEmptyProject = () => {
    setState(s => {
      // pick a unique name
      let i = s.projects.length + 1
      const taken = new Set(s.projects.map(p => p.name))
      let name = `project_${i}`
      while (taken.has(name)) { i += 1; name = `project_${i}` }
      const id = `new_${Date.now()}_${i}`
      return { ...s, projects: [...s.projects, { id, name }] }
    })
  }

  const startIngest = async () => {
    if (!state.uploadId) return
    const activeProjects = state.projects.filter(
      p => state.files.some(f => f.projectId === p.id),
    )
    if (activeProjects.length === 0) {
      setState(s => ({ ...s, step: 'error', error: 'No files selected for any project.' }))
      return
    }

    const payload: UploadProject[] = activeProjects.map(p => {
      const files: UploadFileEntry[] = state.files
        .filter(f => f.projectId === p.id)
        .map(f => ({ name: f.name, relativePath: f.relativePath, uploadPath: f.uploadPath }))
      return {
        name: p.name,
        upload_id: state.uploadId!,
        files,
        name_auto: !p.nameEdited,
      }
    })

    setState(s => ({ ...s, step: 'ingesting', progress: 'Ingesting files…' }))
    try {
      const { job_id } = await uploadIngest(payload)
      pollJob(job_id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, step: 'error', error: msg }))
    }
  }

  const reset = () => {
    stopPoll()
    setState(INITIAL_UPLOAD_STATE)
  }

  // ── render ─────────────────────────────────────────────────────────────────

  if (state.step === 'reviewing') {
    const excluded = state.files.filter(f => f.projectId === null)
    const byProject = new Map<string, ReviewFile[]>()
    for (const f of state.files) {
      if (f.projectId === null) continue
      const list = byProject.get(f.projectId) ?? []
      list.push(f)
      byProject.set(f.projectId, list)
    }
    const projectOptions = state.projects.map(p => ({ value: p.id, label: p.name }))

    return (
      <div className="space-y-4">
        {/* Grouping controls */}
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-slate-300 font-medium mr-1">Group by:</span>
            {([
              ['top', 'Top-level folder'],
              ['leaf', 'Leaf folder'],
              ['single', 'Single project'],
              ['depth', `Depth N`],
            ] as Array<[GroupingMode, string]>).map(([mode, label]) => (
              <button
                key={mode}
                onClick={() => reapplyGrouping(mode, state.depthN)}
                className={`px-3 py-1 rounded text-xs border ${
                  state.grouping === mode
                    ? 'bg-teal-600 border-teal-500 text-white'
                    : 'bg-slate-700 border-slate-600 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {label}
              </button>
            ))}
            {state.grouping === 'depth' && (
              <input
                type="number"
                min={1}
                max={10}
                value={state.depthN}
                onChange={e => {
                  const n = Math.max(1, parseInt(e.target.value || '1', 10))
                  reapplyGrouping('depth', n)
                }}
                className="w-16 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200"
              />
            )}
            <button
              onClick={addEmptyProject}
              className="ml-auto px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded border border-slate-600"
            >
              + New project
            </button>
          </div>
          {state.extracted.length > 0 && (
            <p className="text-xs text-slate-500">
              Extracted {state.extracted.length} archive(s):{' '}
              {state.extracted.map(a => `${a.archive} (${a.count})`).join(', ')}
            </p>
          )}
          {state.failed.length > 0 && (
            <p className="text-xs text-amber-400">
              Failed to extract: {state.failed.map(a => `${a.archive} — ${a.error}`).join('; ')}
            </p>
          )}
        </div>

        {/* Project buckets */}
        <div className="space-y-3">
          {state.projects.map(proj => {
            const filesInProj = byProject.get(proj.id) ?? []
            if (filesInProj.length === 0 && !proj.id.startsWith('new_')) return null
            const totalSize = filesInProj.reduce((s, f) => s + f.size, 0)
            return (
              <div key={proj.id} className="bg-slate-800 border border-slate-700 rounded-lg p-3">
                <div className="flex items-center gap-2 mb-2">
                  <input
                    className="flex-1 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                    value={proj.name}
                    onChange={e => renameProject(proj.id, e.target.value)}
                  />
                  <span className="text-xs text-slate-500 whitespace-nowrap">
                    {filesInProj.length} file(s) · {fmtBytes(totalSize)}
                  </span>
                  <button
                    onClick={() => removeProject(proj.id)}
                    className="px-2 py-1 text-xs bg-slate-700 hover:bg-red-700 text-slate-300 hover:text-white rounded border border-slate-600"
                    title="Exclude all files in this project"
                  >
                    Exclude all
                  </button>
                </div>
                <div className="max-h-60 overflow-y-auto divide-y divide-slate-700/60">
                  {filesInProj.length === 0 && (
                    <p className="text-xs text-slate-500 italic py-2">
                      Empty — move files into this project using the dropdown on each file row.
                    </p>
                  )}
                  {filesInProj.map(f => (
                    <div key={f.id} className="flex items-center gap-2 py-1 text-xs">
                      <span className="flex-1 truncate text-slate-300" title={f.uploadPath}>
                        {f.relativePath}
                        <span className="text-slate-600 ml-2">({f.uploadPath})</span>
                      </span>
                      <span className="text-slate-500 whitespace-nowrap">{fmtBytes(f.size)}</span>
                      <select
                        value={f.projectId ?? '__excl__'}
                        onChange={e => setFileProject(f.id, e.target.value === '__excl__' ? null : e.target.value)}
                        className="bg-slate-700 border border-slate-600 rounded px-1 py-0.5 text-xs text-slate-200"
                      >
                        {projectOptions.map(o => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                        <option value="__excl__">(Excluded)</option>
                      </select>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>

        {/* Excluded section */}
        {excluded.length > 0 && (
          <details className="bg-slate-800/60 border border-slate-700 rounded-lg p-3">
            <summary className="text-sm text-slate-400 cursor-pointer">
              Excluded ({excluded.length})
            </summary>
            <div className="mt-2 max-h-60 overflow-y-auto divide-y divide-slate-700/60">
              {excluded.map(f => (
                <div key={f.id} className="flex items-center gap-2 py-1 text-xs">
                  <span className="flex-1 truncate text-slate-500" title={f.uploadPath}>{f.uploadPath}</span>
                  <span className="text-slate-500 whitespace-nowrap">{fmtBytes(f.size)}</span>
                  <select
                    value="__excl__"
                    onChange={e => setFileProject(f.id, e.target.value === '__excl__' ? null : e.target.value)}
                    className="bg-slate-700 border border-slate-600 rounded px-1 py-0.5 text-xs text-slate-200"
                  >
                    <option value="__excl__">(Excluded)</option>
                    {projectOptions.map(o => (
                      <option key={o.value} value={o.value}>Move to: {o.label}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </details>
        )}

        <div className="flex gap-2">
          <button
            onClick={startIngest}
            className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white rounded text-sm font-medium"
          >
            Ingest {state.files.filter(f => f.projectId !== null).length} file(s)
          </button>
          <button onClick={reset} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm">
            Cancel
          </button>
        </div>
      </div>
    )
  }

  if (state.step === 'uploading' || state.step === 'expanding' || state.step === 'ingesting') {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm text-slate-300">
          <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin" />
          <span>{state.progress}</span>
        </div>
        {state.uploadJob && <JobProgress job={state.uploadJob} />}
      </div>
    )
  }

  if (state.step === 'done') {
    const msg = (state.uploadJob?.result as Record<string, string> | null)?.message ?? 'Ingest complete.'
    return (
      <div className="space-y-3">
        <div className="bg-green-900/50 border border-green-700 rounded-lg px-4 py-3 text-sm text-green-200">
          ✓ {msg}
        </div>
        <button onClick={reset} className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white rounded text-sm">
          Upload more
        </button>
      </div>
    )
  }

  if (state.step === 'error') {
    return (
      <div className="space-y-3">
        <div className="bg-red-900/50 border border-red-700 rounded-lg px-4 py-3 text-sm text-red-200">
          ✗ {state.error}
        </div>
        <button onClick={reset} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm">
          Try again
        </button>
      </div>
    )
  }

  // idle — show drop zone
  return (
    <div
      ref={dropRef}
      onDragEnter={e => { e.preventDefault(); setDragging(true) }}
      onDragOver={e => e.preventDefault()}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
        dragging
          ? 'border-teal-400 bg-teal-900/20'
          : 'border-slate-600 hover:border-slate-500 bg-slate-800/50'
      }`}
    >
      <div className="text-4xl mb-3">📂</div>
      <p className="text-slate-300 font-medium">Drop files or folders here</p>
      <p className="text-slate-500 text-sm mt-1">
        PDFs, DOCX, CSV, XLSX, JSON, TXT, and images are supported.
        <br />Archives (.zip, .tar, .tar.gz, .tar.bz2, .tar.xz) are extracted on the server.
        <br />After upload you can review the file tree and choose how to group files into projects.
      </p>
    </div>
  )
}

// ─── Project detail modal ─────────────────────────────────────────────────────

interface ProjectDetailProps {
  project: Project
  spaces: Space[]
  onClose: () => void
  onJobComplete?: () => void
}

function ProjectDetail({ project, spaces, onClose, onJobComplete }: ProjectDetailProps) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [selectedSpace, setSelectedSpace] = useState<string>(spaces[0]?.space_id ?? '')
  const [selectedSourceType, setSelectedSourceType] = useState<'frame' | 'markdown'>('frame')
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const userSpaces = spaces.filter(s => s.name !== '__global_kg__')

  const loadJobs = useCallback(async () => {
    try {
      const j = await getProjectJobs(project.project_id)
      setJobs(j)
    } catch { /* ignore */ }
  }, [project.project_id])

  useEffect(() => {
    loadJobs()
    return () => { if (pollRef.current) clearTimeout(pollRef.current) }
  }, [loadJobs])

  const pollJob = useCallback((jobId: string) => {
    setActiveJobId(jobId)
    let consecutiveErrors = 0
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        consecutiveErrors = 0
        setJobs(prev => {
          const idx = prev.findIndex(j => j.job_id === jobId)
          if (idx === -1) return [job, ...prev]
          return prev.map(j => j.job_id === jobId ? job : j)
        })
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          pollRef.current = setTimeout(poll, 1000)
        } else {
          setActiveJobId(null)
          if (job.status === 'COMPLETED') {
            // Refresh in-modal job list and notify the parent so the
            // outer table (asset counts, frame status, ...) updates too.
            loadJobs()
            onJobComplete?.()
          }
        }
      } catch (error) {
        consecutiveErrors += 1
        if (shouldStopJobPolling(error, consecutiveErrors)) {
          setActiveJobId(null)
          return
        }
        pollRef.current = setTimeout(poll, nextJobPollDelayMs(consecutiveErrors))
      }
    }
    pollRef.current = setTimeout(poll, 500)
  }, [loadJobs, onJobComplete])

  const runAction = async (fn: () => Promise<{ job_id: string }>) => {
    try {
      const { job_id } = await fn()
      pollJob(job_id)
    } catch (err) {
      console.error('Action failed', err)
    }
  }

  const handleCancel = async () => {
    if (!activeJobId) return
    try {
      await cancelJob(activeJobId)
      setJobs(prev => prev.map(j =>
        j.job_id === activeJobId ? { ...j, status: 'CANCELLED' as const, current_message: 'Cancelling…' } : j
      ))
      setActiveJobId(null)
    } catch (err) {
      console.error('Cancel failed', err)
    }
  }

  const activeJob = jobs.find(j => j.job_id === activeJobId) ?? null

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-slate-700">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-slate-100 truncate">
              {project.label ?? project.source_path ?? project.project_id.slice(0, 12)}
            </h3>
            <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-2 flex-wrap">
              <span>{project.asset_count} asset(s)</span>
              <span className="text-slate-600">·</span>
              <span>Processed: <StatusBadge status={project.processing_status ?? 'UNPROCESSED'} /></span>
              <span className="text-slate-600">·</span>
              <span>Frame: <StatusBadge status={project.frame_status ?? 'NO_FRAME'} /></span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 text-slate-400 hover:text-slate-200 text-xl leading-none"
          >×</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Action buttons */}
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => runAction(() => processProject(project.project_id))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              ⚙ Process files
            </button>
            <button
              onClick={() => runAction(() => extractProject(project.project_id, selectedSpace))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              🧪 Extract frame
            </button>
            <button
              onClick={() => runAction(() => projectToSpace(project.project_id, selectedSpace, selectedSourceType))}
              disabled={!!activeJobId || !selectedSpace}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              📊 Project to space
            </button>
            <button
              onClick={() => runAction(() => kgExtractProject(project.project_id))}
              disabled={!!activeJobId}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded text-sm text-slate-200"
            >
              🕸 Extract graph
            </button>
          </div>

          {/* Space selector */}
          {userSpaces.length > 0 && (
            <div className="space-y-2">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Target space (for extract & project)</label>
                <select
                  value={selectedSpace}
                  onChange={e => setSelectedSpace(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                >
                  {userSpaces.map(s => (
                    <option key={s.space_id} value={s.space_id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Source for &ldquo;Project to space&rdquo;</label>
                <div className="flex gap-4">
                  {(['frame', 'markdown'] as const).map(src => (
                    <label key={src} className="flex items-center gap-1.5 cursor-pointer text-sm text-slate-300">
                      <input
                        type="radio"
                        name={`source-type-${project.project_id}`}
                        value={src}
                        checked={selectedSourceType === src}
                        onChange={() => setSelectedSourceType(src)}
                        className="accent-teal-500"
                      />
                      {src === 'frame' ? 'Frame' : 'Markdown'}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Active job progress */}
          {activeJob && (
            <div className="space-y-2">
              <JobProgress job={activeJob} />
              {(activeJob.status === 'RUNNING' || activeJob.status === 'QUEUED') && (
                <button
                  onClick={handleCancel}
                  className="text-xs px-3 py-1.5 rounded bg-red-900/60 hover:bg-red-800 text-red-300 hover:text-red-200 transition-colors"
                >
                  Cancel job
                </button>
              )}
            </div>
          )}

          {/* Asset list with per-asset processed upload */}
          <ProjectAssetsPanel projectId={project.project_id} />

          {/* Job history */}
          {jobs.length > 0 && (
            <div>
              <h4 className="text-xs text-slate-400 mb-2 uppercase tracking-wide">Recent jobs</h4>
              <div className="space-y-1">
                {jobs.slice(0, 8).map(job => (
                  <div
                    key={job.job_id}
                    className="flex items-center gap-2 px-3 py-1.5 bg-slate-700/50 rounded text-xs"
                  >
                    <StatusBadge status={job.status} />
                    <span className="text-slate-300 flex-1 truncate">{job.label ?? job.kind}</span>
                    <span className="text-slate-500">{job.created_at?.slice(0, 10)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Project assets panel ────────────────────────────────────────────────────

function ProjectAssetsPanel({ projectId }: { projectId: string }) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [processed, setProcessed] = useState<ProcessedAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [uploadTarget, setUploadTarget] = useState<Asset | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [a, p] = await Promise.all([
        listAssets(projectId),
        listProcessedAssets(projectId).catch(() => []),
      ])
      setAssets(a)
      setProcessed(p)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { load() }, [load])

  const processedByAsset = new Map(processed.map(p => [p.asset_id, p]))

  return (
    <div>
      <h4 className="text-xs text-slate-400 mb-2 uppercase tracking-wide">Assets</h4>
      {loading ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : assets.length === 0 ? (
        <p className="text-xs text-slate-500">No assets in this project.</p>
      ) : (
        <ul className="space-y-1">
          {assets.map(a => {
            const proc = processedByAsset.get(a.asset_id)
            return (
              <li
                key={a.asset_id}
                className="flex items-center gap-2 px-3 py-2 bg-slate-700/40 rounded text-xs"
              >
                <span className="text-slate-200 flex-1 truncate" title={a.filename}>
                  {a.filename}
                </span>
                {proc ? (
                  <span
                    className="px-1.5 py-0.5 rounded bg-green-900/40 text-green-300"
                    title={`${proc.processing_type} · ${proc.output_format}`}
                  >
                    processed
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded bg-slate-600 text-slate-300">raw</span>
                )}
                <button
                  onClick={() => setUploadTarget(a)}
                  className="px-2 py-0.5 bg-slate-600 hover:bg-slate-500 rounded text-slate-200"
                  title="Upload your own processed .md (and optional images)"
                >
                  {proc ? 'Replace processed' : 'Upload processed'}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {uploadTarget && (
        <UploadProcessedModal
          asset={uploadTarget}
          onClose={() => setUploadTarget(null)}
          onDone={() => { setUploadTarget(null); load() }}
        />
      )}
    </div>
  )
}

function UploadProcessedModal({
  asset,
  onClose,
  onDone,
}: {
  asset: Asset
  onClose: () => void
  onDone: () => void
}) {
  const [primary, setPrimary] = useState<File | null>(null)
  const [artifacts, setArtifacts] = useState<{ file: File; relativePath: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!primary) {
      setError('Pick a primary file (typically .md).')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const files = [
        { file: primary, relativePath: primary.name },
        ...artifacts,
      ]
      await uploadProcessedAsset(asset.asset_id, files, primary.name)
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-lg w-full max-w-lg">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h3 className="font-semibold text-slate-100 text-sm">
            Upload processed output for <span className="text-teal-300">{asset.filename}</span>
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200 text-lg leading-none">×</button>
        </div>

        <div className="px-5 py-4 space-y-4 text-sm">
          <p className="text-xs text-slate-400">
            Upload a hand-prepared processed bundle (e.g. a Markdown file plus
            any referenced images). The primary file replaces the system's
            processed asset for this raw file.
          </p>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Primary file (.md, .json, .csv, …)</label>
            <input
              type="file"
              accept=".md,.markdown,.txt,.json,.csv,.tsv,.parquet"
              onChange={e => setPrimary(e.target.files?.[0] ?? null)}
              className="block w-full text-xs text-slate-300"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Optional artifacts (images, JSON, …). Folder uploads preserve subpaths.
            </label>
            <input
              type="file"
              multiple
              {...({ webkitdirectory: '', directory: '' } as any)}
              onChange={e => {
                const list = Array.from(e.target.files ?? [])
                setArtifacts(
                  list.map(f => ({
                    file: f,
                    // browsers expose folder-relative path via webkitRelativePath
                    relativePath: (f as any).webkitRelativePath || f.name,
                  })),
                )
              }}
              className="block w-full text-xs text-slate-300"
            />
            {artifacts.length > 0 && (
              <p className="text-xs text-slate-500 mt-1">{artifacts.length} artifact file(s) selected</p>
            )}
          </div>

          {error && (
            <div className="px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-200 text-xs">
              {error}
            </div>
          )}

          <div className="flex gap-2 justify-end pt-2">
            <button
              onClick={onClose}
              disabled={busy}
              className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs"
            >
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={busy || !primary}
              className="px-3 py-1.5 rounded bg-teal-600 hover:bg-teal-500 text-white text-xs font-medium disabled:opacity-50"
            >
              {busy ? 'Uploading…' : 'Upload'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}



// ─── Browse tab ───────────────────────────────────────────────────────────────

function BrowseTab({ spaces }: { spaces: Space[] }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Project | null>(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const data = await listProjects(200)
      setProjects(data)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const getStatus = useCallback(
    (id: string) => projects.find(p => p.project_id === id)?.frame_status ?? 'NO_FRAME',
    [projects],
  )

  if (loading) return <p className="text-slate-400 text-sm">Loading projects…</p>

  return (
    <>
      <ProjectGroupedList
        projects={projects}
        onProjectsChange={setProjects}
        onRefresh={load}
        spaces={spaces}
        getStatus={getStatus}
        emptyMessage="No projects yet — upload files in the Upload tab."
        columns={[
          { header: 'Assets', cellClassName: 'text-slate-400',
            render: p => p.asset_count },
          { header: 'Processed',
            render: p => <StatusBadge status={p.processing_status ?? 'UNPROCESSED'} /> },
          { header: 'Frame',
            render: p => <StatusBadge status={p.frame_status ?? 'NO_FRAME'} /> },
          { header: 'Created', cellClassName: 'text-slate-500 text-xs',
            render: p => p.created_at?.slice(0, 10) },
        ]}
        rowAction={p => (
          <button
            onClick={() => setSelected(p)}
            className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs"
          >
            Manage
          </button>
        )}
      />

      {selected && (
        <ProjectDetail
          project={selected}
          spaces={spaces}
          onClose={() => { setSelected(null); load() }}
          onJobComplete={load}
        />
      )}
    </>
  )
}


// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const [tab, setTab] = useState<'upload' | 'browse'>('upload')
  const [spaces, setSpaces] = useState<Space[]>([])

  useEffect(() => {
    listSpaces().then(setSpaces).catch(() => {})
  }, [])

  return (
    <div className="p-6 max-w-5xl">
      <h2 className="text-xl font-semibold mb-1">Research Projects</h2>
      <p className="text-sm text-slate-400 mb-5">Manage and process your research documents.</p>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-slate-700">
        {(['upload', 'browse'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              tab === t
                ? 'border-teal-500 text-teal-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {t === 'upload' ? 'Upload' : 'Browse'}
          </button>
        ))}
      </div>

      {tab === 'upload' ? <UploadTab /> : <BrowseTab spaces={spaces} />}
    </div>
  )
}
