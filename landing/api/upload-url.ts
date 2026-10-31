// POST /api/upload-url {filename}: a one-time signed upload URL in Storage (clips bucket), so the
// browser can send the full video straight to Supabase (too big for a function body). The floor
// backend then runs the CV pose pipeline on it.
export default async function handler(req: any, res: any) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' })
  const env = (globalThis as any).process?.env ?? {}
  const url = (env.SUPABASE_URL ?? '').replace(/\/$/, '')
  const key = env.SUPABASE_SECRET_KEY
  if (!url || !key) return res.status(503).json({ error: 'uploads are not configured' })
  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body ?? {}
  const ext = (String(body.filename ?? 'clip.mp4').split('.').pop() ?? 'mp4').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 5) || 'mp4'
  const path = `site-demo/uploads/web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`
  const r = await fetch(`${url}/storage/v1/object/upload/sign/clips/${path}`, {
    method: 'POST',
    headers: { apikey: key, authorization: `Bearer ${key}`, 'content-type': 'application/json' },
    body: '{}',
  })
  if (!r.ok) return res.status(502).json({ error: `sign failed ${r.status}` })
  const signed = await r.json()
  const token = new URL(`${url}/storage/v1${signed.url}`).searchParams.get('token')
  return res.status(200).json({ path, token, public_url: `${url}/storage/v1/object/public/clips/${path}` })
}
