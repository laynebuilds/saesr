import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { IncidentView } from '../contracts/incident'
import { CLOUD, MOCK, asset, mediaUrl, videoUrl } from '../data/config'
import { TYPE_LABEL, zoneLabel } from '../lib/format'
import { cn } from '../lib/cn'
import { Dot } from './ui'
import { SEV_COLOR } from './chips'

function useTick() {
  const [t, setT] = useState(() => new Date())
  useEffect(() => {
    const i = setInterval(() => setT(new Date()), 1000)
    return () => clearInterval(i)
  }, [])
  return t
}

const camNo = (c: string) => c.match(/^cam-(\d+)/)?.[1]?.padStart(2, '0') ?? c
const CAM_NAME: Record<string, string> = { 'cam-01': 'warehouse', 'cam-02': 'food line' }
const CAM_SCENE: Record<string, string> = { 'cam-01': asset('/footage/restricted_zone'), 'cam-02': asset('/footage/no_gloves') }

/** CCTV-style frame: feed (live MJPEG or the last evidence frame/clip), scanlines, OSD text. */
export function CameraFeed({
  camera,
  zone,
  incident,
  online = true,
  className,
  big,
}: {
  camera: string
  zone?: string
  incident?: IncidentView | null
  online?: boolean
  className?: string
  big?: boolean
}) {
  const now = useTick()
  const live = !MOCK && !CLOUD // cloud: the recorded incident clip and keyframe, not a live stream
  // Mock: each camera loops its own scene. Live: the backend's annotated MJPEG.
  const clip = MOCK && CAM_SCENE[camera] ? CAM_SCENE[camera] + '.mp4' : mediaUrl(incident?.event.clip_url)
  const still = MOCK && CAM_SCENE[camera] ? CAM_SCENE[camera] + '.jpg' : mediaUrl(incident?.event.frame_url)
  const alert = incident && !['resolved', 'closed', 'dismissed'].includes(incident.status)
  return (
    <div className={cn('cctv relative aspect-video overflow-hidden bg-black', alert && 'flash', className)}>
      {!online ? (
        <div className="grid size-full place-items-center font-mono text-xs tracking-widest text-bad">SIGNAL LOST</div>
      ) : live ? (
        <img src={videoUrl(camera)} alt={`${camera} live`} className="size-full object-cover" />
      ) : clip ? (
        <video key={clip} src={clip} poster={still ?? undefined} autoPlay muted loop playsInline className="size-full object-cover opacity-90 grayscale-[35%]" />
      ) : still ? (
        <img src={still} alt="" className="size-full object-cover opacity-90 grayscale-[35%]" />
      ) : (
        <div className="grid size-full place-items-center font-mono text-xs tracking-widest text-white/40">NO SIGNAL</div>
      )}
      <div className={cn('pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between p-2.5 font-mono text-white/85', big ? 'text-xs' : 'text-[10px]')}>
        <div className="leading-tight">
          <div className="font-semibold tracking-wider">CAM {camNo(camera)}</div>
          <div className="uppercase tracking-wider text-white/55">{zone ? zoneLabel(zone) : CAM_NAME[camera] ?? ''}</div>
        </div>
        <div className="flex items-center gap-1.5 text-right">
          <Dot color="#e5484d" pulse />
          <span className="tracking-wider">{live ? 'LIVE' : MOCK ? 'SAMPLE' : 'REC'}</span>
          <span className="tnum text-white/60">{now.toLocaleTimeString([], { hour12: false })}</span>
        </div>
      </div>
      {alert && incident && (
        <div className="absolute inset-x-0 bottom-0 z-10 flex items-center gap-2 bg-gradient-to-t from-black/90 to-black/40 px-3 py-2 font-mono text-[11px] text-white">
          <span className="h-3 w-1 rounded-sm" style={{ background: SEV_COLOR[incident.severity] }} />
          <span className="font-semibold uppercase tracking-wider">{TYPE_LABEL[incident.event.type]}</span>
          <span className="ml-auto text-white/60">{incident.status.replace('_', ' ').toUpperCase()}</span>
        </div>
      )}
    </div>
  )
}

export function CameraTile({ camera, online, score, incidents }: { camera: string; online: boolean; score: number; incidents: IncidentView[] }) {
  const latest = incidents[0] ?? null
  const active = incidents.filter((i) => !['resolved', 'closed', 'dismissed'].includes(i.status)).length
  const tone = score >= 85 ? 'var(--ready)' : score >= 65 ? 'var(--watch)' : 'var(--critical)'
  return (
    <Link to={`/cameras/${camera}`} className="group glass overflow-hidden rounded-xl transition-colors hover:border-accent">
      <CameraFeed camera={camera} incident={latest} online={online} />
      <div className="flex items-center gap-3 px-3 py-2 font-mono text-[11px]">
        <Dot color={online ? 'var(--ready)' : 'var(--critical)'} />
        {!online && <span className="text-bad">OFFLINE</span>}
        {active > 0 && <span className="text-warn">{active} ACTIVE</span>}
        <span className="tnum ml-auto font-semibold" style={{ color: tone }}>
          {score.toFixed(0)}
        </span>
      </div>
    </Link>
  )
}
