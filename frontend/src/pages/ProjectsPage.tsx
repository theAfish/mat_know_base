import { useState, useRef, useCallback, useEffect } from 'react'
import {
  listProjects, processProject, extractProject, projectToSpace, kgExtractProject, getProjectJobs,
} from '../api/projects'
import { listSpaces } from '../api/spaces'
import { uploadInit, uploadFile, uploadComplete, uploadIngest } from '../api/upload'
import { getJob } from '../api/jobs'
import StatusBadge from '../components/StatusBadge'
import JobProgress from '../components/JobProgress'
import type { Project, Space, Job, UploadProject, UploadFileEntry } from '../types'

// ─── helpers ─────────────────────────────────────────────────────────────────

function slug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
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

interface UploadState {
  step: 'idle' | 'reviewing' | 'uploading' | 'ingesting' | 'done' | 'error'
  pending: Array<{ name: string; editName: string; files: Array<{ file: File; relativePath: string }> }>
  progress: string
  uploadJob: Job | null
  error: string | null
}

function UploadTab() {
  const [state, setState] = useState<UploadState>({
    step: 'idle', pending: [], progress: '', uploadJob: null, error: null,
  })
  const dropRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) clearTimeout(pollRef.current)
  }

  const pollJob = useCallback((jobId: string) => {
    const poll = async () => {
      try {
        const job = await getJob(jobId)
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
      } catch {
        pollRef.current = setTimeout(poll, 2000)
      }
    }
    pollRef.current = setTimeout(poll, 1000)
  }, [])

  useEffect(() => stopPoll, [])

  const onDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const projects = await buildProjectList(e.dataTransfer.items)
    if (projects.length === 0) return
    setState(s => ({
      ...s,
      step: 'reviewing',
      pending: projects.map(p => ({ ...p, editName: slug(p.name) || 'project' })),
      error: null,
    }))
  }, [])

  const startIngest = async () => {
    const { pending } = state
    setState(s => ({ ...s, step: 'uploading', progress: 'Initializing upload…', error: null }))

    try {
      const { upload_id } = await uploadInit()
      const payload: UploadProject[] = []

      for (let pi = 0; pi < pending.length; pi++) {
        const proj = pending[pi]
        const projLabel = proj.editName
        const fileEntries: UploadFileEntry[] = []

        for (let fi = 0; fi < proj.files.length; fi++) {
          const { file, relativePath } = proj.files[fi]
          setState(s => ({
            ...s,
            progress: `Uploading ${projLabel} — ${fi + 1}/${proj.files.length}: ${file.name}`,
          }))
          const uploadPath = `${projLabel}/${relativePath}`
          await uploadFile(upload_id, file, relativePath, uploadPath)
          fileEntries.push({ name: file.name, relativePath, uploadPath })
        }

        payload.push({ name: projLabel, files: fileEntries })
      }

      await uploadComplete(upload_id)
      setState(s => ({ ...s, step: 'ingesting', progress: 'Ingesting files…' }))

      const { job_id } = await uploadIngest(payload)
      pollJob(job_id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, step: 'error', error: msg }))
    }
  }

  const reset = () => {
    stopPoll()
    setState({ step: 'idle', pending: [], progress: '', uploadJob: null, error: null })
  }

  // ── render ─────────────────────────────────────────────────────────────────

  if (state.step === 'reviewing') {
    return (
      <div className="space-y-4">
        <p className="text-sm text-slate-400">
          Review project names before ingesting:
        </p>
        <div className="space-y-2">
          {state.pending.map((proj, i) => (
            <div key={i} className="bg-slate-800 border border-slate-700 rounded-lg p-3 flex items-start gap-3">
              <div className="flex-1 space-y-1">
                <input
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                  value={proj.editName}
                  onChange={e => setState(s => ({
                    ...s,
                    pending: s.pending.map((p, j) => j === i ? { ...p, editName: e.target.value } : p),
                  }))}
                />
                <p className="text-xs text-slate-500">{proj.files.length} file(s)</p>
              </div>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <button
            onClick={startIngest}
            className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white rounded text-sm font-medium"
          >
            Ingest
          </button>
          <button onClick={reset} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm">
            Cancel
          </button>
        </div>
      </div>
    )
  }

  if (state.step === 'uploading' || state.step === 'ingesting') {
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
        <br />Each top-level folder or file becomes a separate project.
      </p>
    </div>
  )
}

// ─── Project detail modal ─────────────────────────────────────────────────────

interface ProjectDetailProps {
  project: Project
  spaces: Space[]
  onClose: () => void
}

function ProjectDetail({ project, spaces, onClose }: ProjectDetailProps) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [selectedSpace, setSelectedSpace] = useState<string>(spaces[0]?.space_id ?? '')
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
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        setJobs(prev => {
          const idx = prev.findIndex(j => j.job_id === jobId)
          if (idx === -1) return [job, ...prev]
          return prev.map(j => j.job_id === jobId ? job : j)
        })
        if (job.status === 'RUNNING' || job.status === 'PENDING') {
          pollRef.current = setTimeout(poll, 1000)
        } else {
          setActiveJobId(null)
        }
      } catch {
        pollRef.current = setTimeout(poll, 2000)
      }
    }
    pollRef.current = setTimeout(poll, 500)
  }, [])

  const runAction = async (fn: () => Promise<{ job_id: string }>) => {
    try {
      const { job_id } = await fn()
      pollJob(job_id)
    } catch (err) {
      console.error('Action failed', err)
    }
  }

  const activeJob = jobs.find(j => j.job_id === activeJobId) ?? null

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <div>
            <h3 className="font-semibold text-slate-100 truncate">
              {project.label ?? project.source_path ?? project.project_id.slice(0, 12)}
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              {project.asset_count} asset(s) · Frame: <StatusBadge status={project.frame_status ?? 'NO_FRAME'} />
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 text-xl leading-none"
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
              onClick={() => runAction(() => projectToSpace(project.project_id, selectedSpace))}
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
          )}

          {/* Active job progress */}
          {activeJob && <JobProgress job={activeJob} />}

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

  if (loading) return <p className="text-slate-400 text-sm">Loading projects…</p>
  if (projects.length === 0) return (
    <p className="text-slate-400 text-sm">No projects yet — upload files in the Upload tab.</p>
  )

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400 uppercase tracking-wide border-b border-slate-700">
              <th className="pb-2 pr-3 font-medium">Label</th>
              <th className="pb-2 pr-3 font-medium">Assets</th>
              <th className="pb-2 pr-3 font-medium">Frame</th>
              <th className="pb-2 pr-3 font-medium">Created</th>
              <th className="pb-2 font-medium"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {projects.map(p => (
              <tr key={p.project_id} className="hover:bg-slate-800/50">
                <td className="py-2 pr-3 text-slate-200 max-w-xs truncate">
                  {p.label ?? p.source_path ?? p.project_id.slice(0, 12)}
                </td>
                <td className="py-2 pr-3 text-slate-400">{p.asset_count}</td>
                <td className="py-2 pr-3">
                  <StatusBadge status={p.frame_status ?? 'NO_FRAME'} />
                </td>
                <td className="py-2 pr-3 text-slate-500 text-xs">{p.created_at?.slice(0, 10)}</td>
                <td className="py-2">
                  <button
                    onClick={() => setSelected(p)}
                    className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs"
                  >
                    Manage
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <ProjectDetail
          project={selected}
          spaces={spaces}
          onClose={() => { setSelected(null); load() }}
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
