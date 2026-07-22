import { useCallback, useEffect, useState } from 'react'

import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import { listAssets, listProcessedAssets } from '../../api/projects'
import { uploadProcessedAsset } from '../../api/upload'
import type { Asset, Job, ProcessedAsset } from '../../types'


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
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setError(err?.response?.data?.detail ?? err?.message ?? 'Upload failed')
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
              {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
              onChange={e => {
                const list = Array.from(e.target.files ?? [])
                setArtifacts(
                  list.map(f => ({
                    file: f,
                    // browsers expose folder-relative path via webkitRelativePath
                    relativePath: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name,
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


export default function ProjectAssetsPanel({ projectId }: { projectId: string }) {
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

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (
        job.status === 'COMPLETED' &&
        job.project_id === projectId &&
        ['process', 'upload'].includes(job.kind)
      ) {
        load()
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load, projectId])

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
