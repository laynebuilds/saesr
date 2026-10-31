// Live source: Layne's backend + the durable actors (contract v2).
//   Site actor  -> POST /api/site/socket grant + WebSocket (full SiteState on every change);
//                  polls GET /site/state if the socket cannot be opened.
//   Agent Relay -> GET /agents/activity, then SSE /agents/stream (message log, optional)
//   Approve / close -> POST /internal/cameras/:id/message (ActorMessage)
import type { ActorMessage, SiteState } from '../contracts/event'
import type { ActivityItem, AgentStatus } from '../contracts/agents'
import { ADMIN_TOKEN, BACKEND_URL, INTERNAL_TOKEN } from './config'
import { addActivity, applySite, getState, markDown, setState } from './store'
import type { Source } from './source'

const BACKOFF_MS = [500, 1000, 2000, 3000, 5000]
const POLL_MS = 1500

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BACKEND_URL + path, { ...init, signal: AbortSignal.timeout(5000) })
  if (!r.ok) throw Object.assign(new Error(`${r.status} ${path}`), { status: r.status })
  return r.json() as Promise<T>
}

function cameraOf(incidentId: string): string {
  for (const c of getState().site?.cameras ?? []) if (c.incidents.some((i) => i.incident_id === incidentId)) return c.camera_id
  throw new Error(`unknown incident ${incidentId}`)
}

async function sendActor(cameraId: string, msg: ActorMessage) {
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (INTERNAL_TOKEN) headers['x-internal-token'] = INTERNAL_TOKEN
  const r = await fetch(`${BACKEND_URL}/internal/cameras/${cameraId}/message`, { method: 'POST', headers, body: JSON.stringify(msg) })
  if (!r.ok) throw new Error(`actor message failed: ${r.status} ${await r.text().catch(() => '')}`.trim())
  return r.json()
}

function agentFeed(stopped: () => boolean): () => void {
  let es: EventSource | null = null
  let attempt = 0
  getJson<{ status: AgentStatus; items: ActivityItem[] }>('/agents/activity?limit=500')
    .then((r) => {
      addActivity(r.items)
      setState({ agentStatus: r.status })
    })
    .catch(() => {})
  const open = () => {
    if (stopped()) return
    es = new EventSource(`${BACKEND_URL}/agents/stream`)
    es.onopen = () => (attempt = 0)
    es.onmessage = (m) => {
      try {
        addActivity([JSON.parse(m.data) as ActivityItem])
      } catch {
        /* a malformed line never breaks the feed */
      }
    }
    es.onerror = () => {
      es?.close()
      setTimeout(open, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)])
    }
  }
  open()
  return () => es?.close()
}

function siteSocket(stopped: () => boolean, onNoActors: () => void) {
  let attempt = 0
  let ws: WebSocket | null = null
  const retry = (why: string) => {
    if (stopped()) return
    markDown('reconnecting', why)
    setTimeout(connect, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)])
  }
  const connect = async () => {
    if (stopped()) return
    let url: string
    try {
      url = (await getJson<{ websocketUrl: string }>('/api/site/socket', { method: 'POST' })).websocketUrl
    } catch (err) {
      const status = (err as { status?: number }).status
      if (status === 404) return onNoActors()
      return retry(status === 503 ? 'actor service unavailable' : 'backend unreachable')
    }
    const sock = new WebSocket(url)
    ws = sock
    let lastSeen = Date.now()
    let gotState = false
    // A socket can open against an actor that is still restarting and then stay silent: ask for
    // the state, and if none arrives (or pongs stop), drop it and reconnect.
    const watchdog = setInterval(() => {
      if (sock.readyState === 1) sock.send('ping')
      const silent = Date.now() - lastSeen
      if ((!gotState && silent > 4000) || silent > 25_000) sock.close()
    }, 2000)
    sock.onopen = () => {
      lastSeen = Date.now()
      setState({ transport: 'socket' })
      sock.send(JSON.stringify({ type: 'refresh' }))
    }
    sock.onmessage = (m) => {
      lastSeen = Date.now()
      if (m.data === 'pong') return
      try {
        const s = JSON.parse(m.data) as SiteState
        if (Array.isArray(s.cameras)) {
          gotState = true
          attempt = 0
          applySite(s)
        }
      } catch {
        /* ignore */
      }
    }
    sock.onclose = () => {
      clearInterval(watchdog)
      if (ws === sock) retry('actor socket closed')
    }
  }
  connect()
  return () => ws?.close()
}

function pollSite(stopped: () => boolean) {
  setState({ transport: 'poll' })
  const tick = async () => {
    if (stopped()) return
    try {
      applySite(await getJson<SiteState>('/site/state'))
    } catch (err) {
      markDown((err as { status?: number }).status === 503 ? 'degraded' : 'reconnecting', 'site state unavailable')
    }
    setTimeout(tick, POLL_MS)
  }
  tick()
}

export function createLiveSource(): Source {
  let stopped = false
  const isStopped = () => stopped
  const stops: (() => void)[] = []
  return {
    start() {
      setState({ mode: 'live', conn: 'connecting' })
      stops.push(agentFeed(isStopped))
      stops.push(siteSocket(isStopped, () => pollSite(isStopped)))
    },
    stop() {
      stopped = true
      stops.forEach((s) => s())
    },
    async approve(actionId, incidentId, user) {
      await sendActor(cameraOf(incidentId), { kind: 'approve', incident_id: incidentId, action_id: actionId, user })
    },
    async ack(incidentId, user) {
      await sendActor(cameraOf(incidentId), { kind: 'close', incident_id: incidentId, reason: `acknowledged by ${user}` })
    },
    async crash() {
      const headers: Record<string, string> = {}
      if (ADMIN_TOKEN) headers['x-admin-token'] = ADMIN_TOKEN
      await fetch(`${BACKEND_URL}/admin/crash?target=actors`, { method: 'POST', headers }).catch(() => {})
    },
  }
}
