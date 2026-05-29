import { useCallback, useEffect, useRef, useState } from 'react'

import { startJobPolling, type JobPollHandle } from '../../api/jobPolling'
import { uploadComplete, uploadExpand, uploadFile, uploadIngest, uploadInit } from '../../api/upload'
import type { UploadExpandFile, UploadFileEntry, UploadProject } from '../../types'
import JobProgress from '../JobProgress'
import {
  INITIAL_UPLOAD_STATE,
  applyGrouping,
  collectFromEntry,
  fmtBytes,
  type GroupingMode,
  type ReviewFile,
  type UploadState,
} from './uploadHelpers'


export default function UploadTab() {
  const [state, setState] = useState<UploadState>(INITIAL_UPLOAD_STATE)
  const dropRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const pollHandleRef = useRef<JobPollHandle | null>(null)

  const stopPoll = () => {
    pollHandleRef.current?.cancel()
    pollHandleRef.current = null
  }

  const pollJob = useCallback((jobId: string) => {
    pollHandleRef.current?.cancel()
    pollHandleRef.current = startJobPolling({
      jobId,
      initialDelayMs: 1000,
      onUpdate: job => setState(s => ({ ...s, uploadJob: job })),
      onComplete: job => setState(s => ({ ...s, step: 'done', uploadJob: job })),
      onFailed: (job, error) => {
        if (job) {
          setState(s => ({
            ...s,
            step: 'error',
            error: job.error ?? 'Ingest failed',
            uploadJob: job,
          }))
        } else if (error) {
          setState(s => ({
            ...s,
            step: 'error',
            error: 'Upload status is no longer available. Please refresh and retry if needed.',
          }))
        }
      },
    })
  }, [])

  useEffect(() => stopPoll, [])

  /** Upload dropped items to temp, expand archives, then go to review. */
  const handleDroppedItems = useCallback(async (items: DataTransferItemList) => {
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
    // Excluding rather than deleting so the bucket survives as a move target;
    // it disappears from the UI once it holds zero files.
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
        return projectId === f.projectId
          ? f
          : { ...f, projectId, relativePath: projectId ? f.name : f.relativePath }
      }),
    }))
  }

  const addEmptyProject = () => {
    setState(s => {
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
                min={0}
                max={10}
                value={state.depthN}
                onChange={e => {
                  const n = Math.max(0, parseInt(e.target.value || '0', 10))
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
