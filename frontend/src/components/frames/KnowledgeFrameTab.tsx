import { useCallback, useEffect, useState } from 'react'

import { getFrame, getFrameHistory } from '../../api/frames'
import { JOB_FINISHED_EVENT } from '../../api/jobPolling'
import { getProject } from '../../api/projects'
import type { ExtractionPass, Frame, Job, Project } from '../../types'
import StatusBadge from '../StatusBadge'
import { FrameHeader, FrameSection } from './frameRender'


export default function KnowledgeFrameTab({ projectId }: { projectId: string }) {
  const [frame, setFrame] = useState<Frame | null>(null)
  const [history, setHistory] = useState<ExtractionPass[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [showRaw, setShowRaw] = useState(false)

  const load = useCallback((showLoading = false) => {
    if (showLoading) setLoading(true)
    Promise.all([getFrame(projectId), getFrameHistory(projectId), getProject(projectId)])
      .then(([f, h, p]) => {
        setFrame(f)
        setHistory(h as unknown as ExtractionPass[])
        setProject(p)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    const refreshOnFinishedJob = (event: Event) => {
      const job = (event as CustomEvent<Job>).detail
      if (
        job.status === 'COMPLETED' &&
        job.project_id === projectId &&
        ['extract', 'raw_workflow', 'canonical_workflow', 'workflow_maintenance'].includes(job.kind)
      ) {
        load()
      }
    }
    window.addEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
    return () => window.removeEventListener(JOB_FINISHED_EVENT, refreshOnFinishedJob)
  }, [load, projectId])

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>
  if (!frame) return <p className="text-sm text-slate-400">No knowledge frame yet. Run Extract to generate one.</p>

  const { content, extraction_summary, extraction_version, status, extracted_at, agent_annotations } = frame
  const clarifications = agent_annotations?.clarifications ?? []
  const resolvedFeedback = agent_annotations?.resolved_feedback ?? []

  return (
    <div className="space-y-4">
      <div className="flex gap-6 flex-wrap text-sm">
        <div><span className="text-slate-400">Status: </span><StatusBadge status={status} /></div>
        <div><span className="text-slate-400">Version: </span><span className="text-slate-200">v{extraction_version}</span></div>
        <div><span className="text-slate-400">Workflow: </span><StatusBadge status={project?.workflow_status ?? 'NO_WORKFLOW'} /></div>
        {extracted_at && <div><span className="text-slate-400">Extracted: </span><span className="text-slate-200">{extracted_at.slice(0, 10)}</span></div>}
      </div>

      {extraction_summary && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-400">
          {extraction_summary}
        </div>
      )}

      {history.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-400 mb-1">Extraction passes</p>
          <div className="space-y-0.5">
            {history.map((h, i) => (
              <div key={i} className="text-xs text-slate-500 px-2 py-1 bg-slate-900 rounded">
                Pass {h.pass_number} ({h.pass_type}) — {h.created_at.slice(0, 10)}
                {h.changes_made ? ' · changes made' : ''}
              </div>
            ))}
          </div>
        </div>
      )}

      {content && (
        <article className="max-w-3xl">
          <FrameHeader paper={(content as Record<string, unknown>).paper} domain={(content as Record<string, unknown>).domain} />
          {Object.entries(content)
            .filter(([k]) => k !== 'paper' && k !== 'domain')
            .map(([key, val]) => (
              <FrameSection key={key} name={key} value={val} depth={1} />
            ))}
        </article>
      )}

      {(clarifications.length > 0 || resolvedFeedback.length > 0) && (
        <div className="border-t border-slate-700 pt-3 space-y-2">
          <p className="text-xs font-medium text-slate-400">Agent Memory</p>
          {clarifications.length > 0 && (
            <details>
              <summary className="text-xs text-teal-400 cursor-pointer">Clarifications ({clarifications.length})</summary>
              <div className="mt-1 space-y-1 pl-2">
                {clarifications.map((c, i) => {
                  const cf = c as Record<string, string>
                  return (
                    <div key={i} className="text-xs text-slate-400 bg-slate-900 rounded px-2 py-1.5">
                      <p><span className="text-slate-500">Q ({cf.field ?? 'general'}):</span> {cf.question ?? ''}</p>
                      <p><span className="text-slate-500">A:</span> {cf.summary ?? ''}</p>
                    </div>
                  )
                })}
              </div>
            </details>
          )}
          {resolvedFeedback.length > 0 && (
            <details>
              <summary className="text-xs text-teal-400 cursor-pointer">Resolved Feedback ({resolvedFeedback.length})</summary>
              <div className="mt-1 space-y-1 pl-2">
                {resolvedFeedback.map((r, i) => {
                  const rf = r as Record<string, string>
                  return (
                    <div key={i} className="text-xs text-slate-400 bg-slate-900 rounded px-2 py-1.5">
                      <p>[{rf.status}] {rf.category ?? ''} ({rf.field_path ?? 'general'}): {rf.question ?? ''}</p>
                      <p><span className="text-slate-500">Resolution:</span> {rf.resolution_notes ?? ''}</p>
                    </div>
                  )
                })}
              </div>
            </details>
          )}
        </div>
      )}

      <div>
        <button onClick={() => setShowRaw(s => !s)} className="text-xs text-teal-400 hover:text-teal-300">
          {showRaw ? '▲ Hide raw JSON' : '▼ Show raw JSON'}
        </button>
        {showRaw && (
          <pre className="mt-2 bg-slate-900 border border-slate-700 rounded-lg p-4 text-xs text-slate-400 overflow-x-auto max-h-96">
            {JSON.stringify(frame, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}
