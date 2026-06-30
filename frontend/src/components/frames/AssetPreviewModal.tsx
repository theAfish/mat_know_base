import { useEffect, useRef, useState } from 'react'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'

GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

type Preview = {
  name: string
  kind: 'pdf' | 'markdown'
  url: string
}

function PdfPage({ document, pageNumber }: { document: PDFDocumentProxy; pageNumber: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let page: PDFPageProxy | null = null
    let cancelled = false
    const canvas = canvasRef.current
    if (!canvas) return

    document.getPage(pageNumber)
      .then(pdfPage => {
        page = pdfPage
        if (cancelled) return

        const viewport = pdfPage.getViewport({ scale: 1.5 })
        const outputScale = window.devicePixelRatio || 1
        const context = canvas.getContext('2d')
        if (!context) throw new Error('Canvas rendering is unavailable')

        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        canvas.style.width = `${Math.floor(viewport.width)}px`
        canvas.style.height = `${Math.floor(viewport.height)}px`

        return pdfPage.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
        }).promise
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not render page')
      })

    return () => {
      cancelled = true
      page?.cleanup()
    }
  }, [document, pageNumber])

  if (error) return <p className="p-4 text-sm text-red-400">Page {pageNumber}: {error}</p>

  return (
    <div className="flex justify-center">
      <canvas ref={canvasRef} className="max-w-none bg-white shadow-lg" aria-label={`Page ${pageNumber}`} />
    </div>
  )
}

function PdfViewer({ document }: { document: PDFDocumentProxy }) {
  return (
    <div className="h-full overflow-auto p-5">
      <div className="mx-auto w-max space-y-5">
        {Array.from({ length: document.numPages }, (_, index) => (
          <PdfPage key={index + 1} document={document} pageNumber={index + 1} />
        ))}
      </div>
    </div>
  )
}

export default function AssetPreviewModal({
  preview,
  onClose,
}: {
  preview: Preview
  onClose: () => void
}) {
  const [markdown, setMarkdown] = useState('')
  const [pdfUrl, setPdfUrl] = useState('')
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setMarkdown('')
    setPdfUrl('')
    setPdfDocument(null)
    let objectUrl = ''
    let loadingTask: PDFDocumentLoadingTask | null = null
    let active = true

    fetch(preview.url, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(body?.detail ?? `Could not load file (${response.status})`)
        }
        if (preview.kind === 'pdf') {
          const data = await response.arrayBuffer()
          const blob = new Blob([data], { type: 'application/pdf' })
          objectUrl = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
          loadingTask = getDocument({ data })
          const loadedDocument = await loadingTask.promise
          if (active) {
            setPdfUrl(objectUrl)
            setPdfDocument(loadedDocument)
          }
          return
        }
        const text = await response.text()
        if (active) setMarkdown(text)
      })
      .catch(err => {
        if (active && err instanceof Error && err.name !== 'AbortError') setError(err.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
      loadingTask?.destroy()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [preview])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview ${preview.name}`}
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="flex h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-950 shadow-2xl">
        <header className="flex items-center gap-3 border-b border-slate-700 px-4 py-3">
          <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100" title={preview.name}>
            {preview.name}
          </h3>
          {(preview.kind === 'markdown' || pdfUrl) && (
            <a
              href={preview.kind === 'pdf' ? pdfUrl : preview.url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-teal-400 hover:text-teal-300"
            >
              Open in new tab
            </a>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-lg leading-none text-slate-400 hover:bg-slate-800 hover:text-white"
            aria-label="Close preview"
          >
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 bg-slate-900">
          {loading ? (
            <p className="p-6 text-sm text-slate-400">Loading preview…</p>
          ) : error ? (
            <p className="p-6 text-sm text-red-400">{error}</p>
          ) : preview.kind === 'pdf' && pdfDocument ? (
            <PdfViewer document={pdfDocument} />
          ) : (
            <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-6 font-mono text-sm leading-6 text-slate-200">
              {markdown}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
