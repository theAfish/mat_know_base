import client from './client'

export interface RuntimeSettings {
  pdf_backend: 'local' | 'mineru_api'
  mineru_api_base: string
  mineru_api_token: string
  mineru_api_token_set?: boolean
  mineru_api_model_version: 'vlm' | 'pipeline'
  mineru_api_language: string
  mineru_api_enable_ocr: boolean
  mineru_api_enable_formula: boolean
  mineru_api_enable_table: boolean
  mineru_api_timeout: number
}

export const getSettings = () =>
  client.get<RuntimeSettings>('/settings').then(r => r.data)

export const updateSettings = (updates: Partial<RuntimeSettings>) =>
  client.put<RuntimeSettings>('/settings', updates).then(r => r.data)
