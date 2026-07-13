/**
 * Centralized configuration for the admin panel.
 *
 * All host/port/path constants live here so they can be overridden
 * via environment variables at build time (VITE_*) or imported in
 * tests / scripts.
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const WS_PATH = import.meta.env.VITE_WS_PATH || '/api/ws'

/**
 * WebSocket endpoint.
 * Falls back to same-origin so a reverse-proxied deploy just works.
 */
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = encodeURIComponent(localStorage.getItem('aigm_api_token') || '')
  return `${proto}//${location.host}${WS_PATH}${token ? `?token=${token}` : ''}`
}

/**
 * Relay admin URL (e.g. the One-Click Relay dashboard).
 * Uses same-origin by default.
 */
function relayAdminUrl() {
  return import.meta.env.VITE_RELAY_ADMIN_URL || ''
}

/**
 * Show API_KEY fields masked after initial load if the server
 * already returned a value.  Users can leave the field blank to
 * keep the existing backend value (write-once pattern).
 */
const SECRET_KEYS = ['llm_api_key', 'relay_api_key']

export { API_BASE, WS_PATH, wsUrl, relayAdminUrl, SECRET_KEYS }
