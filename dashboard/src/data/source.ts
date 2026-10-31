export interface Source {
  start(): void
  stop(): void
  /** Approve a human-gated action: an `approve` ActorMessage; the actor queues it once. */
  approve(actionId: string, incidentId: string, user: string): Promise<void>
  /** Close an incident (a `close` ActorMessage). */
  ack(incidentId: string, user: string): Promise<void>
  /** Kill the actor service for the crash demo (mock simulates it; live calls /admin/crash). */
  crash?(): void | Promise<void>
}
