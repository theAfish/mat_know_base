const LIGHT_STYLES: Record<string, string> = {
  COMPLETED: 'bg-emerald-400',
  RUNNING: 'bg-sky-400',
  IN_PROGRESS: 'bg-sky-400',
  QUEUED: 'bg-amber-400',
  PENDING: 'bg-amber-400',
  FAILED: 'bg-rose-500',
  CANCELLED: 'bg-slate-500',
  NO_FRAME: 'bg-slate-500',
  NO_WORKFLOW: 'bg-slate-500',
  NO_CANONICAL_WORKFLOW: 'bg-slate-500',
  PROCESSED: 'bg-emerald-400',
  UNPROCESSED: 'bg-slate-500',
  PARTIAL: 'bg-amber-400',
  NO_ASSETS: 'bg-slate-500',
}

function normalizeStatus(status: string | null | undefined) {
  return (status ?? '').toUpperCase()
}

function stageTitle(label: string, status: string) {
  return `${label}: ${status}`
}

function Light({ label, status }: { label: string; status: string }) {
  const normalized = normalizeStatus(status)
  const cls = LIGHT_STYLES[normalized] ?? 'bg-slate-500'
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ring-1 ring-inset ring-white/10 ${cls}`}
      title={stageTitle(label, status)}
      aria-label={stageTitle(label, status)}
    />
  )
}

export default function StatusLights({
  processed,
  frame,
  workflow,
  normalized,
}: {
  processed: string
  frame: string
  workflow: string
  normalized: string
}) {
  return (
    <div className="inline-flex items-center gap-1.5 align-middle" aria-label="Project pipeline status">
      <Light label="Processed" status={processed} />
      <Light label="Frame" status={frame} />
      <Light label="Workflow" status={workflow} />
      <Light label="Normalized" status={normalized} />
    </div>
  )
}
