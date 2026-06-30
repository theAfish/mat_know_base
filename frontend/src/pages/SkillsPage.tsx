import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { deleteSkill, getSkill, listSkills, uploadSkill } from '../api/skills'
import type { CustomSkill } from '../types'

export default function SkillsPage() {
  const [skills, setSkills] = useState<CustomSkill[]>([])
  const [selected, setSelected] = useState<CustomSkill | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)

  const refresh = async () => {
    try {
      const next = await listSkills()
      setSkills(next)
      if (selected) {
        const fresh = next.find(skill => skill.skill_id === selected.skill_id)
        setSelected(fresh ? await getSkill(fresh.skill_id) : null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => { refresh() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const handleUpload = async (files: File[]) => {
    if (files.length === 0) return
    setBusy(true); setError(null); setInfo(null)
    try {
      const created = await uploadSkill(files)
      setInfo(`Uploaded ${created.name}.`)
      await refresh()
      setSelected(await getSkill(created.skill_id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const openSkill = async (skill: CustomSkill) => {
    setError(null); setInfo(null)
    try {
      setSelected(await getSkill(skill.skill_id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const removeSkill = async (skill: CustomSkill) => {
    if (!confirm(`Delete skill "${skill.name}"?`)) return
    setBusy(true); setError(null); setInfo(null)
    try {
      await deleteSkill(skill.skill_id)
      setInfo(`Deleted ${skill.name}.`)
      setSelected(null)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700 flex-shrink-0">
        <div>
          <h2 className="text-xl font-semibold">Skills</h2>
          <p className="text-sm text-slate-400">
            Custom SKILL.md bundles that can be attached to space post processors.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            disabled={busy}
            onClick={() => fileRef.current?.click()}
            className="px-3 py-1.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white rounded text-sm transition-colors"
          >
            Upload file or zip
          </button>
          <button
            disabled={busy}
            onClick={() => folderRef.current?.click()}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 rounded text-sm transition-colors"
          >
            Upload folder
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".md,.zip,text/markdown,application/zip"
            className="hidden"
            onChange={e => {
              handleUpload(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />
          <input
            ref={folderRef}
            type="file"
            className="hidden"
            multiple
            onChange={e => {
              handleUpload(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
            {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
          />
        </div>
      </div>

      {(error || info) && (
        <div className="px-6 py-2 flex-shrink-0">
          {error && (
            <div className="px-3 py-2 text-sm rounded bg-rose-900/30 border border-rose-700 text-rose-200">
              {error}
            </div>
          )}
          {info && !error && (
            <div className="px-3 py-2 text-sm rounded bg-emerald-900/30 border border-emerald-700 text-emerald-200">
              {info}
            </div>
          )}
        </div>
      )}

      <div className="flex-1 grid grid-cols-12 gap-4 px-6 py-4 overflow-hidden">
        <div className="col-span-4 overflow-y-auto space-y-1.5">
          {skills.length === 0 && (
            <p className="text-slate-500 text-sm text-center mt-12">
              No custom skills uploaded yet.
            </p>
          )}
          {skills.map(skill => {
            const active = selected?.skill_id === skill.skill_id
            return (
              <button
                key={skill.skill_id}
                onClick={() => openSkill(skill)}
                className={`w-full text-left px-3 py-2 rounded border transition-colors ${
                  active
                    ? 'bg-slate-800 border-teal-500'
                    : 'bg-slate-800/50 border-slate-700 hover:bg-slate-800'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm text-slate-200 truncate">{skill.name}</span>
                  <span className="ml-auto px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 text-[10px]">
                    {skill.source_type}
                  </span>
                </div>
                <div className="mt-0.5 text-xs text-slate-500 truncate">{skill.slug}</div>
                {skill.description && (
                  <div className="mt-1 text-xs text-slate-400 line-clamp-2">{skill.description}</div>
                )}
              </button>
            )
          })}
        </div>

        <div className="col-span-8 overflow-y-auto">
          {selected ? (
            <div className="space-y-4 text-sm pb-6">
              <div className="border border-slate-700 bg-slate-900/50 rounded p-4">
                <div className="flex items-start gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-1.5 py-0.5 rounded bg-teal-900/70 text-teal-200 text-[10px] uppercase tracking-wider">
                        {selected.source_type}
                      </span>
                      <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px]">
                        {selected.file_count} file{selected.file_count === 1 ? '' : 's'}
                      </span>
                    </div>
                    <h3 className="mt-2 text-lg font-semibold text-slate-100 break-words">
                      {selected.name}
                    </h3>
                    <div className="mt-1 text-xs text-slate-500 font-mono">{selected.slug}</div>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => removeSkill(selected)}
                    className="ml-auto px-3 py-1 bg-rose-700 hover:bg-rose-600 disabled:opacity-40 text-white rounded text-xs"
                  >
                    Delete
                  </button>
                </div>
                {selected.description && (
                  <p className="mt-3 text-slate-300 leading-relaxed">{selected.description}</p>
                )}
                {selected.storage_path && (
                  <div className="mt-3 text-xs text-slate-500 font-mono break-all">
                    {selected.storage_path}
                  </div>
                )}
              </div>

              <Section title="Files">
                <div className="flex gap-1.5 flex-wrap">
                  {(selected.metadata.files ?? ['SKILL.md']).map(file => (
                    <span key={file} className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono">
                      {file}
                    </span>
                  ))}
                </div>
              </Section>

              <Section title="SKILL.md">
                <pre className="bg-slate-950/70 border border-slate-800 text-slate-300 text-xs p-3 rounded max-h-[55vh] overflow-auto whitespace-pre-wrap leading-relaxed">
                  {selected.skill_md ?? ''}
                </pre>
              </Section>
            </div>
          ) : (
            <p className="text-slate-500 text-sm text-center mt-12">
              Select a skill to view its bundle.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="border border-slate-700 bg-slate-900/40 rounded p-3">
      <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
        {title}
      </h4>
      {children}
    </section>
  )
}
