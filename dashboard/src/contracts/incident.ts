// The dashboard's view of one incident, adapted from the actor's Incident (contracts/event.ts) by
// src/data/adapt.ts. Pages read only this, so mock and live render the same way.
import type { Action, EventType, IncidentStatus } from './event'
import type { ActivityItem, StandardTag } from './agents'

export type { IncidentStatus }

export type StepName = 'detector' | 'triage' | 'coach' | 'fixer' | 'verifier' | 'reporter'
export type StepState = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface TraceStep {
  agent: StepName
  state: StepState
  model: string | null
  started_at: string | null
  ended_at: string | null
  duration_ms: number | null
  output_summary: string | null
  error: string | null
  tokens: number | null
}

/** What the pages need from the detection behind an incident. */
export interface DisplayEvent {
  id: string
  camera: string
  type: EventType
  zone: string
  timestamp: string
  frame_url: string | null
  clip_url: string | null
  keyframes: string[]
  confidence: number | null
  detail: string | null
}

export interface IncidentView {
  id: string
  event: DisplayEvent
  status: IncidentStatus
  severity: number
  severity_from: 'triage' | 'detector'
  triage_reason: string | null
  coach: { summary: string; recommended_fix: string; standard_tag: StandardTag | string } | null
  verify_summary: string | null
  after_frame_url: string | null
  low_confidence: boolean
  verify_until: string | null
  actions: Action[]
  trace: TraceStep[]
  log: ActivityItem[]
  acknowledged_by: string | null
  updated_at: string
}

export const ACTIVE_STATUSES: IncidentStatus[] = ['verifying', 'confirmed', 'unverified', 'awaiting_approval', 'actioned', 'recurring']
