import { create } from 'zustand'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface ChatState {
  messages: ChatMessage[]
  jobId: string | null
  isThinking: boolean
  currentMessage: string
  addMessage: (msg: ChatMessage) => void
  setJobId: (id: string | null) => void
  setIsThinking: (v: boolean) => void
  setCurrentMessage: (msg: string) => void
  clearChat: () => void
}

export const useChatStore = create<ChatState>(set => ({
  messages: [],
  jobId: null,
  isThinking: false,
  currentMessage: '',
  addMessage: msg => set(s => ({ messages: [...s.messages, msg] })),
  setJobId: id => set({ jobId: id }),
  setIsThinking: v => set({ isThinking: v }),
  setCurrentMessage: msg => set({ currentMessage: msg }),
  clearChat: () => set({ messages: [], jobId: null, isThinking: false, currentMessage: '' }),
}))
