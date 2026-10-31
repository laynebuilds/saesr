const env = import.meta.env

export const MOCK = env.VITE_MOCK !== '0'
export const BACKEND_URL = (env.VITE_BACKEND_URL || 'http://localhost:8000').replace(/\/$/, '')
/** Optional tokens if the backend sets INTERNAL_TOKEN / ADMIN_TOKEN (local demo only). */
export const INTERNAL_TOKEN = env.VITE_INTERNAL_TOKEN || ''
export const ADMIN_TOKEN = env.VITE_ADMIN_TOKEN || ''
export const SUPABASE_URL = env.VITE_SUPABASE_URL || ''
export const SUPABASE_ANON_KEY = env.VITE_SUPABASE_ANON_KEY || ''
export const ACTORS_OBSERVER_URL = env.VITE_ACTORS_OBSERVER_URL || 'http://127.0.0.1:7100'
export { SITE_ID } from '../contracts/event'

/** Backend media paths ("/clips/x.jpg") become absolute; Supabase and mock URLs pass through. */
export function mediaUrl(u: string | null | undefined): string | null {
  if (!u) return null
  if (/^(https?:|data:|blob:)/.test(u) || MOCK) return u
  return BACKEND_URL + (u.startsWith('/') ? u : '/' + u)
}

/** Annotated MJPEG for one camera (backend draws the boxes). */
export const videoUrl = (camera: string) => `${BACKEND_URL}/stream/${camera}`

export const DEMO = new URLSearchParams(location.search).has('demo')
