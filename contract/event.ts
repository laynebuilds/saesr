// Shared contract v2. Mirror of backend/app/schema.py and contract/event.schema.json. Change all three together.

export const SITE_ID = "site-demo";
export type CameraId = "cam-01" | "cam-02"; // cam-01 warehouse lifting + machinery, cam-02 food line hygiene
export type ZoneId = "zone-pack-line" | "zone-machine-a" | "zone-handwash";

export type EventType = "unsafe_lift" | "zone_breach" | "missing_hairnet" | "missing_gloves" | "compliant_observation";

export interface Event {
  event_id: string; // uuid, generated once by the detector; duplicates are dropped
  site_id: string;
  camera_id: string;
  type: EventType;
  confidence: number; // 0..1
  timestamp: string; // ISO 8601 UTC
  zone_id: string;
  bbox?: [number, number, number, number] | null; // [x, y, w, h] normalized 0..1
  centroid?: { x: number; y: number } | null; // normalized 0..1
  metrics: Record<string, unknown>; // e.g. { torso_angle_deg: 62, duration_s: 1.4 }
  keyframes: string[]; // 3 storage URLs (faces blurred)
  clip_url?: string | null;
}

export type IncidentStatus =
  | "verifying" | "confirmed" | "dismissed" | "unverified" | "awaiting_approval"
  | "actioned" | "resolved" | "recurring" | "closed";
export type RelayStep = "triage" | "coach" | "fixer" | "verifier" | "reporter";

export interface TraceEntry {
  agent: string;
  model?: string | null;
  started_at: string;
  ended_at?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

export type ActionKind =
  | "assign_coaching" | "open_corrective_record" | "flag_batch_hold" // tier 0, auto
  | "notify_floor_display" // tier 1, simulated
  | "slow_machine"; // tier 2, human gate, simulated
export type ActionStatus = "pending" | "approved" | "done" | "failed" | "awaiting_approval" | "rejected";

export interface Action {
  action_id: string; // idempotency key: re-sending never executes twice
  incident_id: string;
  kind: ActionKind;
  status: ActionStatus;
  params: Record<string, unknown>;
  approved_by?: string | null;
  executed_at?: string | null;
}

export interface Incident {
  incident_id: string;
  camera_id: string;
  type: EventType;
  zone_id: string;
  status: IncidentStatus;
  severity?: number | null; // 1..5, set by triage
  event_ids: string[];
  opened_at: string;
  updated_at: string;
  relay: Partial<Record<RelayStep, unknown>>;
  actions: Action[];
  trace: TraceEntry[];
  keyframes: string[];
  clip_url?: string | null;
  verify_until?: string | null;
  low_confidence: boolean;
  closed_at?: string | null;
}

// POST /internal/cameras/:id/message body (relay -> actor)
export type ActorMessage =
  | { kind: "detection"; event: Event }
  | { kind: "relay_update"; incident_id: string; step: RelayStep; status: string; output: Record<string, unknown>; trace?: TraceEntry | null }
  | { kind: "action_update"; incident_id: string; action: Action }
  | { kind: "approve"; incident_id: string; action_id: string; user: string }
  | { kind: "close"; incident_id: string; reason?: string | null }
  | { kind: "heartbeat"; fps?: number | null };

// Actor -> POST {RELAY_URL}/relay/start
export interface RelayStart {
  incident_id: string;
  camera_id: string;
  step: RelayStep;
  event: Event;
  keyframes: string[];
  clip_url?: string | null;
  action?: Action | null; // set when asking the relay to execute an approved gated action
}

export interface CameraState {
  camera_id: string;
  site_id: string;
  status: "online" | "offline" | "recovering";
  last_heartbeat?: string | null;
  version: number;
  recovered_from_version?: number | null;
  compliance_score: number;
  counters: Partial<Record<EventType, number>>;
  false_positives_filtered: number;
  autonomous_fixes: number;
  incidents: Incident[];
}

export interface SiteState {
  site_id: string;
  site_score: number;
  active_incidents: number;
  autonomous_fixes: number;
  cameras: CameraState[];
}
