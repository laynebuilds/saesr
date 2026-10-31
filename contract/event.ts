// Mirror of contract/event.schema.json for the dashboard. Change both together.
export type EventType = "unsafe_lift" | "no_hardhat" | "no_vest" | "restricted_zone" | "no_hairnet" | "no_gloves";
export type Severity = "low" | "medium" | "high";

export interface SaesrEvent {
  id: string;
  camera: string;
  type: EventType;
  severity: Severity;
  timestamp: string; // ISO 8601 UTC
  zone: string;
  clip_url?: string | null;
  frame_url?: string | null;
  confidence?: number;
  bbox?: [number, number, number, number] | null;
  detail?: string | null;
}
