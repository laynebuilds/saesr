import { supabase } from '../data/supabase'
import { modelLabel } from '../data/config'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { addLocalIncident } from '../data/store'
import { adaptIncident } from '../data/adapt'
import type { EventType, Incident } from '../contracts/event'
import { AlertTriangle, CheckCircle2, FileVideo, HelpCircle, Loader2, ScanSearch, Trash2, Upload, X } from 'lucide-react'
import { framesFromFile, type Frame } from '../lib/frames'
import { TYPE_LABEL, duration, zoneLabel } from '../lib/format'
import { Button, Card, CardHeader, Empty, Label, Meter, Pill } from '../components/ui'
import { SeverityBadge } from '../components/chips'
import { cn } from '../lib/cn'

interface Violation {
  type: EventType | 'other'
  description: string
  severity: number
  confidence: number
  frame_indices: number[]
  standard_tag: string
  recommended_fix: string
}
interface Analysis {
  verdict: 'violation' | 'no_violation' | 'unclear'
  headline: string
  violations: Violation[]
  compliant_observations: string[]
  people_visible: number
  caveats: string
}
interface Result {
  analysis: Analysis
  model: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
}

type Phase = 'idle' | 'sampling' | 'ready' | 'analyzing' | 'done' | 'error'

const VERDICT = {
  violation: { label: 'Violation', icon: AlertTriangle, color: 'var(--critical)', bg: 'var(--critical-bg)' },
  no_violation: { label: 'No violation', icon: CheckCircle2, color: 'var(--ready)', bg: 'var(--ready-bg)' },
  unclear: { label: 'Unclear', icon: HelpCircle, color: 'var(--watch)', bg: 'var(--watch-bg)' },
} as const

/** On saesr.com: send the full video to Storage so the floor runs the CV pose pipeline on it
 *  (skeleton, detection, incident). Best effort: without it the review still works. */
async function uploadForCv(file: File | null): Promise<string | undefined> {
  if (!file || !file.type.startsWith('video/') || !supabase) return undefined
  try {
    const r = await fetch('/api/upload-url', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ filename: file.name }) })
    if (!r.ok) return undefined
    const { path, token, public_url } = await r.json()
    const { error } = await supabase.storage.from('clips').uploadToSignedUrl(path, token, file, { contentType: file.type || 'video/mp4' })
    return error ? undefined : public_url
  } catch {
    return undefined
  }
}

// The upload survives leaving the page: it is kept here until the user removes it.
const kept: { file: File | null; videoUrl: string | null; frames: Frame[]; dur: number | null; note: string; phase: Phase; result: Result | null } = {
  file: null, videoUrl: null, frames: [], dur: null, note: '', phase: 'idle', result: null,
}

export function Analyze() {
  const [file, setFile] = useState<File | null>(kept.file)
  const [videoUrl, setVideoUrl] = useState<string | null>(kept.videoUrl)
  const [frames, setFrames] = useState<Frame[]>(kept.frames)
  const [dur, setDur] = useState<number | null>(kept.dur)
  const [note, setNote] = useState(kept.note)
  const [phase, setPhase] = useState<Phase>(kept.phase === 'analyzing' || kept.phase === 'sampling' ? (kept.frames.length || kept.file ? 'ready' : 'idle') : kept.phase)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(kept.result)
  const [elapsed, setElapsed] = useState(0)
  const [drag, setDrag] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    Object.assign(kept, { file, videoUrl, frames, dur, note, phase, result })
  }, [file, videoUrl, frames, dur, note, phase, result])
  useEffect(() => {
    if (phase !== 'analyzing') return
    const t0 = Date.now()
    const i = setInterval(() => setElapsed(Date.now() - t0), 100)
    return () => clearInterval(i)
  }, [phase])

  const load = async (f: File) => {
    if (!f.type.startsWith('video/') && !f.type.startsWith('image/')) {
      setError('Upload a video (MP4, MOV, WebM) or a photo.')
      setPhase('error')
      return
    }
    if (videoUrl) URL.revokeObjectURL(videoUrl)
    setFile(f)
    setResult(null)
    setError(null)
    setVideoUrl(URL.createObjectURL(f))
    setPhase('sampling')
    try {
      const { frames, duration } = await framesFromFile(f)
      setFrames(frames)
      setDur(duration)
      setPhase('ready')
    } catch (e) {
      if (f.type.startsWith('video/')) {
        setFrames([]) // the browser cannot read frames; Analyze still sends the video to the floor CV pipeline
        setPhase('ready')
      } else {
        setError((e as Error).message)
        setPhase('error')
      }
    }
  }

  const analyze = async () => {
    setPhase('analyzing')
    setError(null)
    try {
      const video_url = await uploadForCv(file)
      const r = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ filename: file?.name, duration_s: dur ?? undefined, note: note || undefined, video_url, frames: frames.map(({ t, data }) => ({ t, data })) }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body.error ?? `Analysis failed (${r.status})`)
      setResult(body as Result)
      setPhase('done')
    } catch (e) {
      setError((e as Error).message)
      setPhase('error')
    }
  }

  const reset = () => {
    if (videoUrl) URL.revokeObjectURL(videoUrl)
    setFile(null)
    setVideoUrl(null)
    setFrames([])
    setResult(null)
    setError(null)
    setNote('')
    setPhase('idle')
  }

  const seek = (i: number) => {
    const f = frames[i]
    if (f && videoRef.current) {
      videoRef.current.currentTime = f.t
    }
  }

  const flagged = new Map<number, Violation>()
  for (const v of result?.analysis.violations ?? []) for (const i of v.frame_indices) if (!flagged.has(i)) flagged.set(i, v)
  const isImage = file?.type.startsWith('image/')

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between border-b border-line pb-4">
        <div>
          <Label>Footage review</Label>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">Is this a violation?</h1>
        </div>
        {file && (
          <Button variant="ghost" onClick={reset}>
            <X className="size-4" /> New upload
          </Button>
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-7">
          {!file ? (
            <button
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                setDrag(true)
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDrag(false)
                const f = e.dataTransfer.files[0]
                if (f) load(f)
              }}
              className={cn(
                'glass flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-xl border transition-colors',
                drag ? 'border-accent bg-accent-bg' : 'hover:border-accent',
              )}
            >
              <span className="grid size-12 place-items-center rounded-full border border-line">
                <Upload className="size-5 text-accent" />
              </span>
              <span className="text-sm font-medium">Drop footage here, or click to choose</span>
              <span className="font-mono text-[11px] text-dim">MP4 · MOV · WEBM · JPG · PNG</span>
            </button>
          ) : (
            <Card className="overflow-hidden">
              <div className="cctv relative aspect-video bg-black">
                {isImage ? (
                  <img src={videoUrl ?? ''} alt="Upload" className="size-full object-contain" />
                ) : (
                  <video ref={videoRef} src={phase === 'sampling' ? undefined : (videoUrl ?? undefined)} controls muted playsInline autoPlay loop className="size-full object-contain" />
                )}
              </div>
              <div className="flex items-center gap-2 border-t border-line px-4 py-2 font-mono text-[11px] text-muted">
                <FileVideo className="size-3.5" />
                <span className="truncate">{file.name}</span>
                <span className="ml-auto text-dim">
                  {(file.size / 1e6).toFixed(1)} MB{dur ? ` · ${dur.toFixed(1)} s` : ''}
                </span>
                <button onClick={reset} className="ml-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-dim hover:bg-white/5 hover:text-critical" title="Remove this upload">
                  <Trash2 className="size-3.5" /> Remove
                </button>
              </div>
            </Card>
          )}
          <input ref={inputRef} type="file" accept="video/*,image/*" className="hidden" onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />

          {frames.length > 0 && (
            <Card>
              <CardHeader title={`Sampled frames (${frames.length})`} />
              <div className="grid grid-cols-4 gap-2 p-3 sm:grid-cols-8">
                {frames.map((f, i) => {
                  const v = flagged.get(i)
                  return (
                    <button
                      key={i}
                      onClick={() => seek(i)}
                      className={cn('group relative overflow-hidden rounded-md border', v ? 'border-bad ring-1 ring-bad' : 'border-line hover:border-accent')}
                      title={v ? `${label(v.type)} at ${f.t.toFixed(1)} s` : `${f.t.toFixed(1)} s`}
                    >
                      <img src={f.url} alt="" className="aspect-video w-full object-cover" />
                      <span className="absolute bottom-0 left-0 bg-black/70 px-1 font-mono text-[9px] text-white">{f.t.toFixed(1)}s</span>
                      {v && <span className="absolute right-0 top-0 bg-bad px-1 font-mono text-[9px] font-semibold text-white">S{v.severity}</span>}
                    </button>
                  )
                })}
              </div>
            </Card>
          )}

          {(phase === 'ready' || phase === 'done' || (phase === 'error' && frames.length > 0)) && (
            <div className="flex gap-2">
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Optional: what should the vision model system look at? e.g. the worker by the pallet"
                className="h-9 flex-1 rounded-md border border-line bg-card px-3 text-sm placeholder:text-dim focus:border-accent focus:outline-none"
              />
              <Button variant="primary" className="h-9" onClick={analyze}>
                <ScanSearch className="size-4" />
                {phase === 'done' ? 'Analyze again' : 'Analyze'}
              </Button>
            </div>
          )}
        </div>

        <div className="lg:col-span-5">
          {phase === 'idle' && (
            <Card>
              <CardHeader title="How it works" />
              <ol className="space-y-3 p-4 text-sm text-muted">
                <li>
                  <span className="font-mono text-dim">01</span> Your browser samples up to 8 frames evenly across the clip. The video itself never leaves your laptop.
                </li>
                <li>
                  <span className="font-mono text-dim">02</span> The vision model system checks them for unsafe lifts, restricted zone entry, missing hairnets, gloves, hard hats and vests.
                </li>
                <li>
                  <span className="font-mono text-dim">03</span> You get a verdict, the frames it is based on, the standard, and a fix. Click a finding to jump to that moment.
                </li>
              </ol>
            </Card>
          )}

          {phase === 'sampling' && (
            <Card className="flex items-center gap-3 p-5 text-sm text-muted">
              <Loader2 className="size-4 animate-spin text-accent" /> Sampling frames…
            </Card>
          )}

          {phase === 'analyzing' && (
            <Card className="p-5">
              <div className="flex items-center gap-3">
                <Loader2 className="size-5 animate-spin text-accent" />
                <div>
                  <div className="font-medium">Reviewing {frames.length} frames</div>
                  <div className="tnum font-mono text-xs text-dim">vision model system · {(elapsed / 1000).toFixed(1)} s</div>
                </div>
              </div>
              <Meter value={Math.min(0.95, elapsed / 20000)} className="mt-4" />
            </Card>
          )}

          {phase === 'error' && error && (
            <Card className="border-bad/40 p-4 text-sm">
              <div className="flex items-start gap-2 text-bad">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}
              </div>
            </Card>
          )}

          {phase === 'done' && result && file && <ResultPanel r={result} frames={frames} onSeek={seek} file={file} note={note} />}
        </div>
      </div>
    </div>
  )
}

const EXTRA_LABEL: Record<string, string> = { damaged_packaging: 'Damaged packaging', other: 'Other hazard' }
const label = (t: string) => TYPE_LABEL[t as EventType] ?? EXTRA_LABEL[t] ?? 'Other hazard'

const CAMERA_FOR: Record<string, string> = { unsafe_lift: 'cam-01', zone_breach: 'cam-01', missing_hairnet: 'cam-02', missing_gloves: 'cam-02' }

/** Turns a footage review into a dashboard incident (this browser session), shaped exactly like an
 *  actor incident with the review as its triage and coach steps, so it shows up in Incidents, the
 *  fix report and the compliance log. */
function logIncident(r: Result, frames: Frame[], file: File, zone: string, note: string): string | null {
  const v = r.analysis.violations.find((x) => x.type !== 'other')
  if (!v) return null
  const now = Date.now()
  const id = `inc-upload-${now}`
  const frame = frames[v.frame_indices[0] ?? 0] ?? frames[0]
  const started = new Date(now - r.latency_ms).toISOString()
  const ended = new Date(now).toISOString()
  const camera = CAMERA_FOR[v.type] ?? 'cam-01'
  const tokens = { input_tokens: r.input_tokens, output_tokens: r.output_tokens }
  const inc: Incident = {
    incident_id: id,
    camera_id: camera,
    type: v.type as EventType,
    zone_id: zone,
    status: 'confirmed',
    severity: v.severity,
    event_ids: [`evt-upload-${now}`],
    opened_at: started,
    updated_at: ended,
    relay: {
      detector: { detail: `Uploaded footage: ${file.name}${note ? `. Note: ${note}` : ''}`, confidence: v.confidence },
      triage: { status: 'confirmed', severity: v.severity, reason: v.description, ...tokens },
      coach: { status: 'done', summary: r.analysis.headline, recommended_fix: v.recommended_fix, standard_tag: v.standard_tag },
    } as Incident['relay'],
    actions: [],
    trace: [
      { agent: 'triage', model: r.model, started_at: started, ended_at: ended, output_summary: v.description },
      { agent: 'coach', model: r.model, started_at: started, ended_at: ended, output_summary: v.recommended_fix },
    ],
    keyframes: frame ? [frame.url] : [],
    clip_url: file.type.startsWith('video/') ? URL.createObjectURL(file) : null,
    verify_until: null,
    low_confidence: false,
    closed_at: null,
  }
  const cam = { camera_id: camera, site_id: 'site-demo', status: 'online', version: 0, compliance_score: 100, counters: {}, false_positives_filtered: 0, autonomous_fixes: 0, incidents: [inc] } as const
  addLocalIncident(adaptIncident(inc, { ...cam, incidents: [inc] }))
  return id
}

function ResultPanel({ r, frames, onSeek, file, note }: { r: Result; frames: Frame[]; onSeek: (i: number) => void; file: File; note: string }) {
  const a = r.analysis
  const v = VERDICT[a.verdict]
  const navigate = useNavigate()
  const zones = ['zone-pack-line', 'zone-machine-a', 'zone-handwash']
  const [zone, setZone] = useState(zones[0] ?? 'unknown')
  const loggable = a.violations.some((x) => x.type !== 'other')
  return (
    <div className="rise space-y-4">
      <Card className="overflow-hidden">
        <div className="flex items-center gap-4 p-5" style={{ background: v.bg }}>
          <v.icon className="size-9 shrink-0" style={{ color: v.color }} />
          <div>
            <div className="font-mono text-xs font-semibold uppercase tracking-[0.18em]" style={{ color: v.color }}>
              {v.label}
            </div>
            <div className="mt-1 text-lg font-semibold leading-snug">{a.headline}</div>
          </div>
        </div>
        <div className="grid grid-cols-3 divide-x divide-line border-t border-line font-mono text-[11px]">
          <div className="px-4 py-2">
            <div className="text-dim">FINDINGS</div>
            <div className="tnum text-sm text-fg">{a.violations.length}</div>
          </div>
          <div className="px-4 py-2">
            <div className="text-dim">PEOPLE</div>
            <div className="tnum text-sm text-fg">{a.people_visible}</div>
          </div>
          <div className="px-4 py-2">
            <div className="text-dim">REVIEW TIME</div>
            <div className="tnum text-sm text-fg">{duration(r.latency_ms)}</div>
          </div>
        </div>
      </Card>

      {a.violations.map((x, i) => (
        <Card key={i} className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{label(x.type)}</span>
            <SeverityBadge severity={x.severity} />
            <Pill className="text-muted">{x.standard_tag}</Pill>
          </div>
          <p className="mt-2 text-sm">{x.description}</p>
          <p className="mt-2 text-sm">
            <span className="font-medium">Fix: </span>
            <span className="text-muted">{x.recommended_fix}</span>
          </p>
          <div className="mt-3 flex items-center gap-3">
            <span className="label">Confidence</span>
            <Meter value={x.confidence} tone={x.confidence > 0.75 ? 'ok' : 'warn'} className="w-24" />
            <span className="tnum font-mono text-xs text-muted">{Math.round(x.confidence * 100)}%</span>
            <span className="ml-auto flex flex-wrap gap-1">
              {x.frame_indices
                .filter((fi) => frames[fi])
                .map((fi) => (
                  <button key={fi} onClick={() => onSeek(fi)} className="rounded border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted hover:border-accent hover:text-accent">
                    {frames[fi].t.toFixed(1)}s
                  </button>
                ))}
            </span>
          </div>
        </Card>
      ))}

      {a.compliant_observations.length > 0 && (
        <Card>
          <CardHeader title="Done right" />
          <ul className="space-y-1.5 p-4 text-sm">
            {a.compliant_observations.map((o) => (
              <li key={o} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> <span className="text-muted">{o}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {a.caveats && (
        <Card className="p-4 text-sm">
          <Label>Limits of this review</Label>
          <p className="mt-1.5 text-muted">{a.caveats}</p>
        </Card>
      )}

      {a.violations.length === 0 && a.verdict !== 'violation' && !a.compliant_observations.length && <Empty>No findings.</Empty>}

      {loggable && (
        <Card className="flex flex-wrap items-center gap-2 p-3">
          <span className="text-sm text-muted">Log this as an incident in</span>
          <select value={zone} onChange={(e) => setZone(e.target.value)} className="h-8 rounded-md border border-line bg-card px-2 text-sm">
            {zones.map((z) => (
              <option key={z} value={z}>
                {zoneLabel(z)}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            className="ml-auto"
            onClick={() => {
              const id = logIncident(r, frames, file, zone, note)
              if (id) navigate(`/incidents/${id}`)
            }}
          >
            Log as incident
          </Button>
        </Card>
      )}

      <div className="font-mono text-[10px] text-dim">
        {modelLabel(r.model)} · {r.input_tokens.toLocaleString()} in / {r.output_tokens.toLocaleString()} out tokens · AI review, confirm before acting
      </div>
    </div>
  )
}
