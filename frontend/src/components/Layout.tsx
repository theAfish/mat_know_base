import type { ReactNode } from 'react'
import { useUiStore } from '../store/uiStore'
import type { Page } from '../types'

const NAV_ITEMS: { page: Page; label: string; icon: string }[] = [
  { page: 'assistant',    label: 'Assistant',          icon: '🤖' },
  { page: 'projects',    label: 'Projects',            icon: '📁' },
  { page: 'frames',      label: 'Knowledge Frames',    icon: '🧩' },
  { page: 'graph',       label: 'Dataset Graph',       icon: '🕸️' },
  { page: 'projections', label: 'Projections',         icon: '📊' },
  { page: 'feedback',    label: 'Feedback',            icon: '💬' },
]

interface Props { children: ReactNode }

export default function Layout({ children }: Props) {
  const { page, setPage } = useUiStore()

  return (
    <div className="flex h-screen bg-slate-900 text-slate-200 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-slate-800 border-r border-slate-700 flex flex-col">
        <div className="px-4 py-5 border-b border-slate-700">
          <h1 className="text-lg font-bold text-teal-400">🔬 MKB</h1>
          <p className="text-xs text-slate-400 mt-0.5">Materials Knowledge Base</p>
        </div>
        <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
          {NAV_ITEMS.map(item => (
            <button
              key={item.page}
              onClick={() => setPage(item.page)}
              className={`w-full text-left px-3 py-2 rounded-md text-sm flex items-center gap-2.5 transition-colors ${
                page === item.page
                  ? 'bg-teal-600 text-white font-medium'
                  : 'text-slate-300 hover:bg-slate-700 hover:text-slate-100'
              }`}
            >
              <span className="text-base leading-none">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
