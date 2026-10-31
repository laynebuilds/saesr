import { useMemo } from 'react'
import { useDash } from '../data/store'
import type { CameraState } from '../contracts/event'
import type { IncidentView } from '../contracts/incident'

export interface CameraRow {
  camera: string
  online: boolean
  status: CameraState['status']
  score: number
  incidents: IncidentView[]
}

/** Cameras as the actors report them; score is the actor's own compliance_score. */
export function useCameras(incidents: IncidentView[]): CameraRow[] {
  const site = useDash((s) => s.site)
  return useMemo(
    () =>
      (site?.cameras ?? []).map((c) => ({
        camera: c.camera_id,
        online: c.status !== 'offline',
        status: c.status,
        score: c.compliance_score,
        incidents: incidents.filter((i) => i.event.camera === c.camera_id),
      })),
    [site, incidents],
  )
}
