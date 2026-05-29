import { useEffect, useState } from 'react'

import { listAssets, listProcessedAssets } from '../../api/projects'
import type { Asset, ProcessedAsset } from '../../types'


export default function AssetsTab({ projectId }: { projectId: string }) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [processed, setProcessed] = useState<ProcessedAsset[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([listAssets(projectId), listProcessedAssets(projectId)])
      .then(([a, p]) => { setAssets(a); setProcessed(p) })
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (assets.length === 0) return <p className="text-sm text-slate-400">No assets ingested yet.</p>

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium text-slate-400 mb-2">{assets.length} raw asset(s)</p>
        <div className="space-y-0.5">
          {assets.map(a => (
            <div key={a.asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
              <span className="flex-1 truncate text-slate-300">{a.filename}</span>
              <span className="text-slate-500">{a.mime_type ?? '—'}</span>
              <span className="text-slate-500">{a.status ?? '—'}</span>
            </div>
          ))}
        </div>
      </div>
      {processed.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-400 mb-2">{processed.length} processed output(s)</p>
          <div className="space-y-0.5">
            {processed.map(p => (
              <div key={p.processed_asset_id} className="flex gap-4 text-xs text-slate-400 px-2 py-1.5 bg-slate-900 rounded">
                <span className="flex-1 truncate text-slate-300">{p.filename ?? '—'}</span>
                <span className="text-slate-500">{p.processing_type}</span>
                <span className="text-slate-500">.{p.output_format}</span>
                <span className="text-slate-600">{p.artifact_count} artifact(s)</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
