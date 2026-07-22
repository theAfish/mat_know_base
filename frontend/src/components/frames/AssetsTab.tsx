import { useCallback, useEffect, useState } from 'react'

import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import { listAssets, listProcessedAssets } from '../../api/projects'
import type { Asset, Job, ProcessedAsset } from '../../types'
import AssetPreviewModal from './AssetPreviewModal'

type Preview = {
  name: string
  kind: 'pdf' | 'markdown'
  url: string
}

export default function AssetsTab({ projectId }: { projectId: string }) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [processed, setProcessed] = useState<ProcessedAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)

  const load = useCallback((showLoading = false) => {
    if (showLoading) setLoading(true)
    Promise.all([listAssets(projectId), listProcessedAssets(projectId)])
      .then(([a, p]) => { setAssets(a); setProcessed(p) })
      .finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load(true) }, [load])

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

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (assets.length === 0) return <p className="text-sm text-slate-400">No assets ingested yet.</p>

  const rawPreview = (asset: Asset): Preview | null => {
    const isPdf = asset.filename.toLowerCase().endsWith('.pdf') || asset.mime_type === 'application/pdf'
    const isMarkdown = /\.(md|markdown)$/i.test(asset.filename)
    if (!isPdf && !isMarkdown) return null
    return {
      name: asset.filename,
      kind: isPdf ? 'pdf' : 'markdown',
      url: `/api/projects/${projectId}/assets/${asset.asset_id}/content`,
    }
  }

  const processedPreview = (asset: ProcessedAsset): Preview | null => {
    const name = asset.primary_relpath ?? `processed.${asset.output_format}`
    if (!/\.(md|markdown)$/i.test(name)) return null
    return {
      name,
      kind: 'markdown',
      url: `/api/projects/${projectId}/processed-assets/${asset.processed_asset_id}/content`,
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium text-slate-400 mb-2">{assets.length} raw asset(s)</p>
        <div className="space-y-0.5">
          {assets.map(a => {
            const target = rawPreview(a)
            return (
              <div key={a.asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
                {target ? (
                  <button
                    type="button"
                    onClick={() => setPreview(target)}
                    className="flex-1 truncate text-left text-teal-400 hover:text-teal-300 hover:underline"
                    title={`Preview ${a.filename}`}
                  >
                    {a.filename}
                  </button>
                ) : (
                  <span className="flex-1 truncate text-slate-300">{a.filename}</span>
                )}
                <span className="text-slate-500">{a.mime_type ?? '—'}</span>
                <span className="text-slate-500">{a.status ?? '—'}</span>
              </div>
            )
          })}
        </div>
      </div>
      {processed.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-400 mb-2">{processed.length} processed output(s)</p>
          <div className="space-y-0.5">
            {processed.map(p => {
              const target = processedPreview(p)
              const displayName = p.primary_relpath ?? p.filename ?? `processed.${p.output_format}`
              return (
                <div key={p.processed_asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
                  {target ? (
                    <button
                      type="button"
                      onClick={() => setPreview(target)}
                      className="flex-1 truncate text-left text-teal-400 hover:text-teal-300 hover:underline"
                      title={`Preview ${displayName}`}
                    >
                      {displayName}
                    </button>
                  ) : (
                    <span className="flex-1 truncate text-slate-300">{displayName}</span>
                  )}
                  <span className="text-slate-500">{p.processing_type}</span>
                  <span className="text-slate-500">.{p.output_format}</span>
                  <span className="text-slate-600">{p.artifact_count} artifact(s)</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {preview && <AssetPreviewModal preview={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
