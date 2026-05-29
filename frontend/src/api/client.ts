import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 60_000,
})

/** Dispatched whenever any API response carries a job_id, so the job panel
 *  can immediately poll instead of waiting for the next slow-poll cycle. */
export const JOB_STARTED_EVENT = 'mkb:job-started'

client.interceptors.response.use(
  r => {
    if (r.data && typeof r.data === 'object' && 'job_id' in r.data) {
      window.dispatchEvent(new CustomEvent(JOB_STARTED_EVENT, { detail: { job_id: r.data.job_id } }))
    }
    return r
  },
  err => {
    const detail = err?.response?.data?.detail
    if (detail) {
      err.message = typeof detail === 'string' ? detail : JSON.stringify(detail)
    }
    return Promise.reject(err)
  },
)

export default client
