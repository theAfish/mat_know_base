import { useState, useRef, useEffect } from 'react'
import { sendChatMessage } from '../api/assistant'
import { useChatStore } from '../store/chatStore'
import { useJobPolling } from '../hooks/useJobPolling'
import type { Job } from '../types'

export default function AssistantPage() {
  const {
    messages, jobId, isThinking, currentMessage,
    addMessage, setJobId, setIsThinking, setCurrentMessage, clearChat,
  } = useChatStore()

  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useJobPolling({
    jobId,
    onProgress: (job: Job) => {
      setCurrentMessage(job.current_message ?? 'Thinking...')
    },
    onComplete: (job: Job) => {
      const result = job.result as Record<string, unknown> | null ?? {}
      const reply =
        (result.reply as string) ||
        ((job.events ?? [])
          .filter(e => e.stage === 'agent_text')
          .slice(-1)[0]?.message) ||
        '(No response)'
      addMessage({ role: 'assistant', content: reply })
      setJobId(null)
      setIsThinking(false)
      setCurrentMessage('')
    },
    onFailed: (job: Job) => {
      addMessage({
        role: 'assistant',
        content: `Sorry, an error occurred: ${job.error ?? 'Unknown error'}`,
      })
      setJobId(null)
      setIsThinking(false)
      setCurrentMessage('')
    },
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isThinking])

  const handleSend = async () => {
    const msg = input.trim()
    if (!msg || isThinking) return
    setInput('')
    addMessage({ role: 'user', content: msg })
    setIsThinking(true)
    setCurrentMessage('Thinking...')
    try {
      const { job_id } = await sendChatMessage(msg)
      setJobId(job_id)
    } catch {
      addMessage({ role: 'assistant', content: 'Failed to send message. Please try again.' })
      setIsThinking(false)
      setCurrentMessage('')
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700 flex-shrink-0">
        <div>
          <h2 className="text-xl font-semibold">Assistant</h2>
          <p className="text-sm text-slate-400">
            Ask me to check your projects, run workflows, or explain the system state.
          </p>
        </div>
        <button
          onClick={clearChat}
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm transition-colors"
        >
          Clear
        </button>
      </div>

      {/* Chat history */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !isThinking && (
          <p className="text-slate-500 text-center mt-12 text-sm">
            Ask me anything about your research data.
          </p>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-2xl px-4 py-2.5 rounded-xl text-sm whitespace-pre-wrap leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-teal-700 text-white rounded-br-sm'
                  : 'bg-slate-700 text-slate-100 rounded-bl-sm'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {isThinking && (
          <div className="flex justify-start">
            <div className="max-w-2xl px-4 py-2.5 rounded-xl text-sm bg-slate-700 text-slate-400 italic rounded-bl-sm flex items-center gap-2">
              <span className="inline-block w-3 h-3 border-2 border-slate-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />
              <span>{currentMessage || 'Thinking...'}</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-6 py-4 border-t border-slate-700 flex-shrink-0">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            disabled={isThinking}
            placeholder={isThinking ? 'Thinking...' : 'Tell me what to do...'}
            className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-4 py-2.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-teal-500 disabled:opacity-50 transition-colors"
          />
          <button
            onClick={handleSend}
            disabled={isThinking || !input.trim()}
            className="px-4 py-2.5 bg-teal-600 hover:bg-teal-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
