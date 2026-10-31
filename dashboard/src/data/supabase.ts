// Supabase is the log, read for history only, with the publishable (read-only) key.
// Live state never comes from here; it comes from the Site actor.
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './config'

export const supabase: SupabaseClient | null =
  SUPABASE_URL && SUPABASE_ANON_KEY ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY, { auth: { persistSession: false } }) : null

/** A logged incident row. Field names differ between sink versions, so read defensively. */
export interface HistoryRow {
  id: string
  camera: string
  type: string
  zone: string
  severity: string
  ts: string
  status: string | null
  detail: string | null
  frame_url: string | null
  bbox: number[] | null
}

export function normalizeRow(r: Record<string, unknown>): HistoryRow {
  const s = (k: string) => (r[k] == null ? null : String(r[k]))
  return {
    id: s('id') ?? s('incident_id') ?? s('event_id') ?? '',
    camera: s('camera') ?? s('camera_id') ?? '',
    type: s('type') ?? '',
    zone: s('zone') ?? s('zone_id') ?? '',
    severity: s('severity') ?? '',
    ts: s('ts') ?? s('timestamp') ?? s('opened_at') ?? s('created_at') ?? '',
    status: s('status'),
    detail: s('detail'),
    frame_url: s('frame_url'),
    bbox: Array.isArray(r.bbox) ? (r.bbox as number[]) : null,
  }
}

export async function fetchHistory(limit = 500): Promise<HistoryRow[]> {
  if (!supabase) throw new Error('Supabase is not configured (VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY)')
  const { data, error } = await supabase.from('incidents').select('*').limit(limit)
  if (error) throw new Error(`${error.message}${error.code === '42501' ? ' (row-level security: add a read-only select policy)' : ''}`)
  return (data ?? []).map(normalizeRow).sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
}

/** Supabase Realtime: new or changed incident rows, so history stays current while open. */
export function watchHistory(onRow: (r: HistoryRow) => void): () => void {
  if (!supabase) return () => {}
  const ch = supabase
    .channel('saesr-history')
    .on('postgres_changes', { event: '*', schema: 'public', table: 'incidents' }, (p) => {
      if (p.new && Object.keys(p.new).length) onRow(normalizeRow(p.new as Record<string, unknown>))
    })
    .subscribe()
  return () => {
    supabase?.removeChannel(ch)
  }
}
