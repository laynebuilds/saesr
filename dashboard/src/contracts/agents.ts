// Agent Relay activity feed from the backend (/agents/activity, /agents/stream). Optional: the
// incident lifecycle itself comes from the actors (contracts/event.ts); this is the message log.
export interface ActivityItem {
  id: string
  ts: string
  kind: 'message' | 'action'
  agent: string
  to: string
  text: string
  event_id: string | null
  relay_id: string | null
  action: { type: string; event_ids?: string[]; [k: string]: unknown } | null
}

export interface AgentStatus {
  enabled: boolean
  connected: boolean
  relay_url: string | null
  channel: string | null
  agents: string[]
  error: string | null
}

export type StandardTag = 'OSHA ergonomics' | 'OSHA PPE' | 'FSMA' | 'HACCP' | 'GMP'
