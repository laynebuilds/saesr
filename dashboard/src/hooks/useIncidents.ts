import { useMemo } from 'react'
import { adaptSite } from '../data/adapt'
import { useDash } from '../data/store'
import type { IncidentView } from '../contracts/incident'

export function useIncidents(): IncidentView[] {
  const site = useDash((s) => s.site)
  const activity = useDash((s) => s.activity)
  const local = useDash((s) => s.local)
  return useMemo(
    () => [...local, ...adaptSite(site, activity)].sort((a, b) => Date.parse(b.event.timestamp) - Date.parse(a.event.timestamp)),
    [site, activity, local],
  )
}

export function useIncident(id: string | undefined): IncidentView | null {
  const all = useIncidents()
  return useMemo(() => all.find((i) => i.id === id) ?? null, [all, id])
}
