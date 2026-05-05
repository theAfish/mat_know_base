import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 60_000,
})

export default client
