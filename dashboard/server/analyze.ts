// POST /api/analyze: a supervisor uploads footage, the browser samples frames, and Claude reviews
// them for the same violations the CV pipeline flags. Runs inside the Vite dev server so the API
// key stays server-side (ANTHROPIC_API_KEY in .env.local, never VITE_-prefixed, never bundled).
import type { Plugin } from 'vite'
import Anthropic from '@anthropic-ai/sdk'
import { z } from 'zod'
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod'

const MODEL = 'claude-opus-5-5'
const MAX_FRAMES = 10
const MAX_BODY = 12 * 1024 * 1024

export const AnalysisSchema = z.object({
  verdict: z.enum(['violation', 'no_violation', 'unclear']),
  headline: z.string().describe('One sentence a floor manager can act on.'),
  violations: z.array(
    z.object({
      type: z.enum(['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'other']),
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
export type Analysis = z.infer<typeof AnalysisSchema>

const SYSTEM = `You review workplace footage for a food plant and warehouse safety team. A supervisor uploaded a clip (or a photo) they filmed or were sent and wants to know whether it shows a violation.

You get frames sampled in time order, each labeled with its index and timestamp. Check for:
- unsafe_lift: lifting or lowering a load by bending at the waist with straight or locked legs, twisting while loaded, or lifting from the floor without bending the knees.
- zone_breach: a person inside a marked machine or vehicle zone while equipment is running or moving.
- missing_hairnet: hair uncovered where open food is handled.
- missing_gloves: bare hands touching open food or food-contact surfaces.
- other: a clear, serious hazard outside these types (for example a missing hard hat or hi-vis where clearly required).

Rules:
- Report only what is visible in the frames. If a frame is ambiguous, say so in caveats and lower confidence; if nothing can be determined, the verdict is "unclear".
- Do not identify, name, or describe anyone's face or identity. Refer to people by position ("worker on the left").
- Severity 1 to 5, where 5 is imminent danger of serious injury or a likely food safety lapse reaching product.
- Keep descriptions short and factual. The headline must be a single plain sentence.`

function readBody(req: import('http').IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => {
      size += c.length
      if (size > MAX_BODY) {
        reject(new Error('upload too large'))
        req.destroy()
      } else chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

const RequestSchema = z.object({
  filename: z.string().max(300).optional(),
  duration_s: z.number().optional(),
  note: z.string().max(1000).optional(),
  frames: z
    .array(z.object({ t: z.number(), data: z.string().min(100) }))
    .min(1)
    .max(MAX_FRAMES),
})

export function analyzePlugin(apiKey: string | undefined): Plugin {
  return {
    name: 'saesr-analyze',
    configureServer(server) {
      server.middlewares.use('/api/analyze', async (req, res) => {
        const send = (status: number, body: unknown) => {
          res.statusCode = status
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify(body))
        }
        if (req.method !== 'POST') return send(405, { error: 'POST only' })
        if (!apiKey) return send(503, { error: 'ANTHROPIC_API_KEY is not set in dashboard/.env.local. Add it and restart the dev server.' })

        let input: z.infer<typeof RequestSchema>
        try {
          input = RequestSchema.parse(JSON.parse(await readBody(req)))
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
          send(200, {
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
      })
    },
  }
}
