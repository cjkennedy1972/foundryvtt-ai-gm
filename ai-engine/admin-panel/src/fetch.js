/**
 * Centralized fetch wrapper for all network requests.
 *
 * - Checks `res.ok` and throws with the server error message on failure
 * - Consistent error handling across all API calls
 * - Centralized location for adding auth headers, retries, etc.
 */
import { API_BASE } from './config.js'

export async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`

  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }

  const token = localStorage.getItem('aigm_api_token') || import.meta.env.VITE_API_TOKEN
  if (token && !headers.Authorization) headers.Authorization = `Bearer ${token}`

  const config = {
    ...options,
    headers,
  }

  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    config.body = JSON.stringify(options.body)
  }

  const res = await fetch(url, config)

  let data
  try {
    data = await res.json()
  } catch {
    if (!res.ok) {
      throw new Error(`Server error (${res.status} ${res.statusText})`)
    }
    return { ok: true, data: null }
  }

  if (!res.ok) {
    // Prefer server-provided error message
    const msg = data?.error || data?.message || `Server error (${res.status})`
    throw new Error(msg)
  }

  return { ok: true, data }
}

/**
 * Thin wrapper for status OK check — returns { ok, data } or { ok: false, error }.
 */
export async function safeFetch(path, options = {}) {
  try {
    const result = await apiFetch(path, options)
    return result
  } catch (e) {
    return { ok: false, error: e.message }
  }
}
