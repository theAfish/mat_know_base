import client from './client'

export const sendChatMessage = (message: string): Promise<{ job_id: string }> =>
  client.post('/assistant/chat', { message }).then(r => r.data)
