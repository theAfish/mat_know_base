const STATUS_COLORS: Record<string, string> = {
  COMPLETED:   'bg-green-800 text-green-100',
  RUNNING:     'bg-yellow-800 text-yellow-100',
  IN_PROGRESS: 'bg-yellow-800 text-yellow-100',
  PENDING:     'bg-slate-700 text-slate-300',
  FAILED:      'bg-red-800 text-red-100',
  NO_FRAME:    'bg-slate-700 text-slate-400',
  REVIEWED:    'bg-teal-800 text-teal-100',
  DRAFT:       'bg-blue-800 text-blue-100',
  OPEN:        'bg-orange-800 text-orange-100',
  RESOLVED:    'bg-green-800 text-green-100',
  DISMISSED:   'bg-slate-700 text-slate-400',
}

export default function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status?.toUpperCase()] ?? 'bg-slate-700 text-slate-300'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}
