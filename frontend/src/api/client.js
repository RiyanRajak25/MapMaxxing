import axios from 'axios'

const configuredApiBase = import.meta.env.VITE_API_BASE
const API_BASE = configuredApiBase
  ? /^https?:\/\//.test(configuredApiBase)
    ? configuredApiBase.replace(/\/$/, '')
    : `https://${configuredApiBase}`
  : 'http://localhost:8000'
const EXPORT_TIMEOUT_MS = 180000

const client = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
})

export async function runChangeDetection({
  aoi,
  year1,
  year2,
  cloudThreshold,
  includeWetland,
}) {
  try {
    // Periods are years, not date ranges. The backend expands each to the
    // post-monsoon dry season starting in that year, so the two composites
    // always sample the same phenology.
    const { data } = await client.post('/api/change-detection', {
      aoi,
      year1,
      year2,
      cloud_threshold: cloudThreshold,
      include_wetland: includeWetland,
    })
    return data
  } catch (error) {
    throw new Error(extractMessage(error))
  }
}

export async function downloadShapefile(exportUrls, onStatus) {
  const { data: job } = await client.post('/api/export-shapefile', { export_urls: exportUrls })
  let status = job
  const startedAt = Date.now()
  while (status.status === 'preparing') {
    if (Date.now() - startedAt > EXPORT_TIMEOUT_MS) {
      throw new Error('The shapefile export timed out. Try a smaller area or run the analysis again.')
    }
    await new Promise((resolve) => setTimeout(resolve, 1500))
    const { data } = await client.get(`/api/export-shapefile/${job.job_id}`)
    status = data
    onStatus(status.detail || status.status)
  }
  if (status.status === 'error') throw new Error(status.detail)
  window.location.assign(`${API_BASE}${status.download_url}`)
}

function extractMessage(error) {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as an array of field-level objects.
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg).join('; ')
  }
  if (error.code === 'ERR_NETWORK') {
    return 'Cannot reach the backend. Make sure it is running on ' + API_BASE
  }
  return error.message || 'Something went wrong.'
}
