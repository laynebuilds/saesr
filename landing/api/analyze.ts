// POST /api/analyze on saesr.com: the public demo's "Review footage". Same prompt, schema and model as
// dashboard/server/analyze.ts (the local dev-server version); the key lives in the Vercel project env.
import Anthropic from '@anthropic-ai/sdk'
import { z } from 'zod'
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod'

const MODEL = 'claude-opus-5-5'
const MAX_FRAMES = 10

export const AnalysisSchema = z.object({
  verdict: z.enum(['violation', 'no_violation', 'unclear']),
  headline: z.string().describe('One sentence a floor manager can act on.'),
  violations: z.array(
    z.object({
      type: z.enum(['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'damaged_packaging', 'other']),
      description: z.string().describe('What is visible, in one or two sentences.'),
      severity: z.number().int().min(1).max(5),
      confidence: z.number().min(0).max(1),
      frame_indices: z.array(z.number().int()).describe('0-based indices of the frames where it is visible.'),
      standard_tag: z.enum(['OSHA ergonomics', 'OSHA PPE', 'FSMA', 'HACCP', 'GMP']),
      recommended_fix: z.string(),
    }),
  ),
  compliant_observations: z.array(z.string()).describe('Things done correctly, e.g. gloves worn at the line.'),
  people_visible: z.number().int().min(0),
  caveats: z.string().describe('What limits this review: image quality, angle, occlusion. Empty if none.'),
})

const SYSTEM = `You review workplace footage for a food plant and warehouse safety team. A supervisor uploaded a clip (or a photo) they filmed or were sent and wants to know whether it shows a violation.

You get frames sampled in time order, each labeled with its index and timestamp. Check for:
- unsafe_lift: lifting or lowering a load by bending at the waist with straight or locked legs, twisting while loaded, or lifting from the floor without bending the knees.
- zone_breach: a person inside a marked machine or vehicle zone while equipment is running or moving.
- missing_hairnet: hair uncovered where open food is handled.
- missing_gloves: bare hands touching open food or food-contact surfaces.
- damaged_packaging: a box or load being handled or stacked that is open, torn, crushed or split, has loose, torn or failing stretch wrap or strapping, or is unstable or leaning, so its contents could fall on a worker or be damaged. Say what is wrong with it.
- other: a clear, serious hazard outside these types (for example a missing hard hat or hi-vis where clearly required).

Rules:
- Report only what is visible in the frames. If a frame is ambiguous, say so in caveats and lower confidence; if nothing can be determined, the verdict is "unclear".
- Do not identify, name, or describe anyone's face or identity. Refer to people by position ("worker on the left").
- Severity 1 to 5, where 5 is imminent danger of serious injury or a likely food safety lapse reaching product.
- Keep descriptions short and factual. The headline must be a single plain sentence.`


const RequestSchema = z.object({
  filename: z.string().max(300).optional(),
  duration_s: z.number().optional(),
  note: z.string().max(1000).optional(),
  frames: z
    .array(z.object({ t: z.number(), data: z.string().min(100) }))
    .min(1)
    .max(MAX_FRAMES),
})


// A violation found in uploaded footage becomes a real detection: its frames go to Storage and a row
// goes to web_detections, which the floor backend picks up and runs through the full pipeline
// (actors, relay agents over Agent Relay, manager call or text by severity).
const PIPELINE_TYPES = new Set(['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'damaged_packaging'])

async function logDetection(input: z.infer<typeof RequestSchema>, analysis: z.infer<typeof AnalysisSchema>) {
  const env = (globalThis as any).process?.env ?? {}
  const url = (env.SUPABASE_URL ?? '').replace(/\/$/, '')
  const key = env.SUPABASE_SECRET_KEY
  const v = analysis.violations.filter((x) => PIPELINE_TYPES.has(x.type)).sort((a, b) => b.severity - a.severity)[0]
  if (analysis.verdict !== 'violation' || !v) return { logged: false, reason: 'no pipeline violation' }
  if (!url || !key) return { logged: false, reason: 'Supabase not configured' }
  const headers = { apikey: key, authorization: `Bearer ${key}` }
  const stamp = `web-${Date.now()}`
  const picks = (v.frame_indices.length ? v.frame_indices : [0]).slice(0, 3)
  const keyframes: string[] = []
  for (const [n, i] of picks.entries()) {
    const f = input.frames[Math.min(i, input.frames.length - 1)]
    const path = `site-demo/${stamp}_k${n}.jpg`
    const r = await fetch(`${url}/storage/v1/object/keyframes/${path}`, {
      method: 'POST',
      headers: { ...headers, 'content-type': 'image/jpeg', 'x-upsert': 'true' },
      body: Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0)),
    })
    if (r.ok) keyframes.push(`${url}/storage/v1/object/public/keyframes/${path}`)
  }
  const r = await fetch(`${url}/rest/v1/web_detections`, {
    method: 'POST',
    headers: { ...headers, 'content-type': 'application/json', prefer: 'return=representation' },
    body: JSON.stringify({ type: v.type, severity: v.severity, confidence: v.confidence, summary: v.description, keyframes }),
  })
  if (!r.ok) return { logged: false, reason: `log failed ${r.status}` }
  const [row] = await r.json()
  return { logged: true, id: row?.id, type: v.type, severity: v.severity }
}

export default async function handler(req: any, res: any) {
  const send = (status: number, body: unknown) => res.status(status).json(body)
  if (req.method !== 'POST') return send(405, { error: 'POST only' })
  const apiKey = (globalThis as any).process?.env?.ANTHROPIC_API_KEY as string | undefined
  if (!apiKey) return send(503, { error: 'Footage review is not configured on this deployment.' })
  let input: z.infer<typeof RequestSchema>
  try {
    input = RequestSchema.parse(typeof req.body === 'string' ? JSON.parse(req.body) : req.body)
  } catch (err) {
    return send(400, { error: `bad request: ${(err as Error).message}` })
  }
    const client = new Anthropic({ apiKey })
    const content: Anthropic.Beta.BetaContentBlockParam[] = []
    input.frames.forEach((f, i) => {
      content.push({ type: 'text', text: `Frame ${i} at ${f.t.toFixed(1)} s` })
      content.push({ type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: f.data } })
    })
    content.push({
      type: 'text',
      text: [
        `File: ${input.filename ?? 'upload'}${input.duration_s ? `, ${input.duration_s.toFixed(1)} s long` : ''}, ${input.frames.length} frames sampled.`,
        input.note ? `Supervisor's note: ${input.note}` : '',
        'Review the frames and return the analysis.',
      ]
        .filter(Boolean)
        .join('\n'),
    })

    const started = Date.now()
    try {
      const msg = await client.beta.messages.create({
        model: MODEL,
        max_tokens: 16000,
        betas: ['server-side-fallback-2026-07-01'],
        fallbacks: 'default',
        output_config: { effort: 'medium', format: zodOutputFormat(AnalysisSchema) },
        system: SYSTEM,
        messages: [{ role: 'user', content }],
      })
      if (msg.stop_reason === 'refusal') return send(422, { error: 'The model declined to review this footage.' })
      const text = msg.content.flatMap((b) => (b.type === 'text' ? [b.text] : [])).join('')
      const analysis = AnalysisSchema.parse(JSON.parse(text))
      const detection = await logDetection(input, analysis).catch((err) => ({ error: (err as Error).message }))
      send(200, {
        detection,
        analysis,
        model: 'vision model system',
        latency_ms: Date.now() - started,
        input_tokens: msg.usage.input_tokens,
        output_tokens: msg.usage.output_tokens,
      })
    } catch (err) {
      if (err instanceof Anthropic.AuthenticationError) return send(401, { error: 'The vision model system key was rejected.' })
      if (err instanceof Anthropic.RateLimitError) return send(429, { error: 'Rate limited. Try again in a few seconds.' })
      if (err instanceof Anthropic.APIError) return send(502, { error: `Vision model error ${err.status}: ${err.message}` })
      send(500, { error: (err as Error).message })
    }
}
