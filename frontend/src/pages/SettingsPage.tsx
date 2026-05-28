import { useEffect, useState } from 'react'
import { getSettings, updateSettings, type RuntimeSettings } from '../api/settings'

export default function SettingsPage() {
  const [data, setData] = useState<RuntimeSettings | null>(null)
  const [tokenInput, setTokenInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const s = await getSettings()
      setData(s)
      setTokenInput('')
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleSave = async () => {
    if (!data) return
    setSaving(true)
    setError(null)
    try {
      const payload: Partial<RuntimeSettings> = {
        pdf_backend: data.pdf_backend,
        mineru_api_base: data.mineru_api_base,
        mineru_api_model_version: data.mineru_api_model_version,
        mineru_api_language: data.mineru_api_language,
        mineru_api_enable_ocr: data.mineru_api_enable_ocr,
        mineru_api_enable_formula: data.mineru_api_enable_formula,
        mineru_api_enable_table: data.mineru_api_enable_table,
        mineru_api_timeout: data.mineru_api_timeout,
        extraction_model: data.extraction_model,
        vision_model: data.vision_model,
        log_level: data.log_level,
        max_concurrent_jobs: data.max_concurrent_jobs,
      }
      // Only send token when user typed something new (avoid wiping with masked value)
      if (tokenInput.trim() !== '') {
        payload.mineru_api_token = tokenInput.trim()
      }
      const updated = await updateSettings(payload)
      setData(updated)
      setTokenInput('')
      setSavedAt(Date.now())
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const clearToken = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await updateSettings({ mineru_api_token: '' })
      setData(updated)
      setTokenInput('')
      setSavedAt(Date.now())
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to clear token')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="p-6 text-slate-400">Loading…</div>
  }
  if (!data) {
    return <div className="p-6 text-red-400">{error ?? 'Failed to load settings'}</div>
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-slate-100">Settings</h1>
        <p className="text-sm text-slate-400 mt-1">
          Configure how the system processes PDFs and other runtime preferences.
          Changes are persisted to <code className="text-slate-300">data/runtime_settings.json</code>.
        </p>
      </header>

      {error && (
        <div className="px-4 py-3 rounded bg-red-900/40 border border-red-700 text-red-200 text-sm">
          {error}
        </div>
      )}
      {savedAt && !error && (
        <div className="px-4 py-3 rounded bg-green-900/30 border border-green-700 text-green-200 text-sm">
          Saved.
        </div>
      )}

      {/* PDF Backend */}
      <section className="bg-slate-800 border border-slate-700 rounded-lg p-5 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">PDF Processing Backend</h2>
          <p className="text-xs text-slate-400 mt-1">
            How PDFs uploaded to MKB are converted into Markdown.
          </p>
        </div>

        <div className="space-y-2">
          {([
            { value: 'local', label: 'Local (MinerU VLM)', desc: 'Runs the MinerU model on this machine. No internet required; needs a GPU for reasonable speed.' },
            { value: 'mineru_api', label: 'MinerU Cloud API', desc: 'Sends PDFs to MinerU\'s cloud service. Requires an API token from mineru.net.' },
          ] as const).map(opt => (
            <label
              key={opt.value}
              className={`flex gap-3 p-3 rounded border cursor-pointer transition-colors ${
                data.pdf_backend === opt.value
                  ? 'border-teal-500 bg-teal-500/10'
                  : 'border-slate-700 hover:border-slate-600'
              }`}
            >
              <input
                type="radio"
                name="pdf_backend"
                className="mt-1"
                checked={data.pdf_backend === opt.value}
                onChange={() => setData({ ...data, pdf_backend: opt.value })}
              />
              <div>
                <div className="font-medium text-slate-100 text-sm">{opt.label}</div>
                <div className="text-xs text-slate-400 mt-0.5">{opt.desc}</div>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* MinerU API config */}
      {data.pdf_backend === 'mineru_api' && (
        <section className="bg-slate-800 border border-slate-700 rounded-lg p-5 space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">MinerU API Credentials</h2>
            <p className="text-xs text-slate-400 mt-1">
              Get a token at{' '}
              <a className="text-teal-400 hover:underline" href="https://mineru.net/apiManage/token" target="_blank" rel="noreferrer">
                mineru.net/apiManage/token
              </a>
              . See{' '}
              <a className="text-teal-400 hover:underline" href="https://mineru.net/apiManage/docs" target="_blank" rel="noreferrer">
                API docs
              </a>.
            </p>
          </div>

          <Field label="API Base URL">
            <input
              type="text"
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
              value={data.mineru_api_base}
              onChange={e => setData({ ...data, mineru_api_base: e.target.value })}
            />
          </Field>

          <Field label="API Token" hint={data.mineru_api_token_set ? 'A token is currently saved.' : 'No token set.'}>
            <div className="flex gap-2">
              <input
                type="password"
                placeholder={data.mineru_api_token_set ? '(unchanged — leave blank to keep)' : 'Paste your MinerU token'}
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
                value={tokenInput}
                onChange={e => setTokenInput(e.target.value)}
              />
              {data.mineru_api_token_set && (
                <button
                  type="button"
                  onClick={clearToken}
                  disabled={saving}
                  className="px-3 py-2 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-200 disabled:opacity-50"
                >
                  Clear
                </button>
              )}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Model Version">
              <select
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
                value={data.mineru_api_model_version}
                onChange={e => setData({ ...data, mineru_api_model_version: e.target.value as 'vlm' | 'pipeline' })}
              >
                <option value="vlm">vlm (recommended)</option>
                <option value="pipeline">pipeline</option>
              </select>
            </Field>

            <Field label="Language">
              <input
                type="text"
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
                value={data.mineru_api_language}
                onChange={e => setData({ ...data, mineru_api_language: e.target.value })}
              />
            </Field>
          </div>

          <div className="grid grid-cols-3 gap-3 text-sm">
            <Checkbox
              label="Enable OCR"
              checked={data.mineru_api_enable_ocr}
              onChange={v => setData({ ...data, mineru_api_enable_ocr: v })}
            />
            <Checkbox
              label="Enable Formula"
              checked={data.mineru_api_enable_formula}
              onChange={v => setData({ ...data, mineru_api_enable_formula: v })}
            />
            <Checkbox
              label="Enable Table"
              checked={data.mineru_api_enable_table}
              onChange={v => setData({ ...data, mineru_api_enable_table: v })}
            />
          </div>

          <Field label="Timeout (seconds)">
            <input
              type="number"
              min={30}
              max={3600}
              className="w-32 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
              value={data.mineru_api_timeout}
              onChange={e => setData({ ...data, mineru_api_timeout: Number(e.target.value) || 600 })}
            />
          </Field>
        </section>
      )}

      {/* Agent / LLM */}
      <section className="bg-slate-800 border border-slate-700 rounded-lg p-5 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Agent / LLM</h2>
          <p className="text-xs text-slate-400 mt-1">
            Model used for knowledge extraction and projection. Changes take effect for the next job.
          </p>
        </div>

        <Field label="Extraction Model" hint='LiteLLM model string, e.g. "openai/gpt-4o" or "openai/glm-5.1".'>
          <input
            type="text"
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 font-mono"
            value={data.extraction_model}
            onChange={e => setData({ ...data, extraction_model: e.target.value })}
          />
        </Field>

        <Field label="Vision Model" hint='Multimodal model for image tools. Leave blank to use the extraction model.'>
          <input
            type="text"
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 font-mono"
            placeholder="(same as extraction model)"
            value={data.vision_model}
            onChange={e => setData({ ...data, vision_model: e.target.value })}
          />
        </Field>
      </section>

      {/* System */}
      <section className="bg-slate-800 border border-slate-700 rounded-lg p-5 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">System</h2>
          <p className="text-xs text-slate-400 mt-1">
            Log verbosity and job concurrency. <span className="text-amber-400">Max concurrent jobs requires a server restart.</span>
          </p>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Log Level">
            <select
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
              value={data.log_level}
              onChange={e => setData({ ...data, log_level: e.target.value as RuntimeSettings['log_level'] })}
            >
              <option value="DEBUG">DEBUG – verbose (agent dialogs, traces)</option>
              <option value="INFO">INFO – concise app messages</option>
              <option value="WARNING">WARNING – warnings and errors only</option>
              <option value="ERROR">ERROR – errors only</option>
            </select>
          </Field>

          <Field label="Max Concurrent Jobs" hint="Requires server restart.">
            <input
              type="number"
              min={1}
              max={32}
              className="w-32 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100"
              value={data.max_concurrent_jobs}
              onChange={e => setData({ ...data, max_concurrent_jobs: Math.max(1, Number(e.target.value) || 1) })}
            />
          </Field>
        </div>
      </section>

      <div className="flex gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white rounded text-sm font-medium disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Settings'}
        </button>
        <button
          onClick={load}
          disabled={saving}
          className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-sm disabled:opacity-50"
        >
          Reload
        </button>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-slate-400 mb-1">{label}</label>
      {children}
      {hint && <p className="text-xs text-slate-500 mt-1">{hint}</p>}
    </div>
  )
}

function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-slate-200 cursor-pointer">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}
