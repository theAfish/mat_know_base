import type { Job } from '../types'

interface Props {
  job: Job | null
  title?: string
}

export default function JobProgress({ job, title }: Props) {
  if (!job) return null

  const isRunning = job.status === 'RUNNING' || job.status === 'PENDING'
  const events = job.events?.slice(-5) ?? []

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg p-3 text-sm space-y-1.5">
      <div className="flex items-center gap-2">
        {isRunning && (
          <span className="inline-block w-3 h-3 border-2 border-teal-400 border-t-transparent rounded-full animate-spin" />
        )}
        <span className="font-medium text-slate-200">{title ?? job.label ?? job.kind}</span>
        <span className={`ml-auto text-xs px-2 py-0.5 rounded font-medium ${
          job.status === 'COMPLETED' ? 'bg-green-800 text-green-100' :
          job.status === 'FAILED'    ? 'bg-red-800 text-red-100' :
          job.status === 'RUNNING'   ? 'bg-yellow-800 text-yellow-100' :
          'bg-slate-700 text-slate-300'
        }`}>{job.status}</span>
      </div>

      {job.current_message && isRunning && (
        <p className="text-slate-400 text-xs italic">{job.current_message}</p>
      )}

      {events.length > 0 && (
        <ul className="space-y-0.5">
          {events.map((ev, i) => (
            <li key={i} className="text-xs text-slate-500">· {ev.message}</li>
          ))}
        </ul>
      )}

      {job.status === 'FAILED' && job.error && (
        <p className="text-red-400 text-xs">Error: {job.error}</p>
      )}

      {job.status === 'COMPLETED' && job.result && (
        <p className="text-green-400 text-xs">
          {(job.result as Record<string, string>).message ?? 'Completed successfully.'}
        </p>
      )}
    </div>
  )
}
