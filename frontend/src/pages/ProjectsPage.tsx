import { useEffect, useState } from 'react'

import { listSpaces } from '../api/spaces'
import BrowseTab from '../components/projects/BrowseTab'
import UploadTab from '../components/projects/UploadTab'
import type { Space } from '../types'


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
