import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 60_000,
})

client.interceptors.response.use(
  r => r,
  err => {
    const detail = err?.response?.data?.detail
    if (detail) {
      err.message = typeof detail === 'string' ? detail : JSON.stringify(detail)
    }
    return Promise.reject(err)
  },
)

export default client
