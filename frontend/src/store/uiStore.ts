import { create } from 'zustand'
import type { Page } from '../types'

interface UiState {
  page: Page
  setPage: (page: Page) => void
}

export const useUiStore = create<UiState>(set => ({
  page: 'assistant',
  setPage: page => set({ page }),
}))
