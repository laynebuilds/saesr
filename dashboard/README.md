# saesr dashboard

React + Vite + TypeScript + Tailwind v4. Reads live state from the durable actors (contract v2, `contract/event.ts`, copied to `src/contracts/event.ts`).

```bash
cd dashboard
npm install
cp .env.example .env.local     # VITE_MOCK=1 runs with no backend
npm run dev                    # http://localhost:5173  (add ?demo=1 for the crash button)
```

Live mode: run the stack (`scripts/run_demo.sh` at the repo root), set `VITE_MOCK=0`, restart `npm run dev`.

| Need | Backend call |
| --- | --- |
| Live state | `POST /api/site/socket` grant, then the WebSocket (full `SiteState` on every change); falls back to polling `GET /site/state` |
| Camera video | `GET /stream/:camera_id` (MJPEG) |
| Approve a gated action | `POST /internal/cameras/:id/message` `{kind: "approve", incident_id, action_id, user}` |
| Acknowledge (close) | same route, `{kind: "close", incident_id, reason}` |
| Crash demo | `POST /admin/crash?target=actors` (run_demo.sh restarts it) |
| Agent Relay log | `GET /agents/activity`, SSE `/agents/stream` |
| History | Supabase (publishable key, read-only) |

Pages: Overview (needs-approval bar, readouts, camera wall, live incidents, relay funnel, zone risk), Cameras, Incidents, Incident detail (fix report with before/after, relay trace, coaching), Review footage (upload a clip, Claude reviews sampled frames; needs `ANTHROPIC_API_KEY` in `.env.local`, server-side only), Compliance log (CSV export, cost estimate from your own assumptions), Agent relay, Analytics.

`src/data/adapt.ts` turns actor `Incident`s into the view model the pages render; `src/data/mock.ts` is a mock actor that emits the same `SiteState` by the same rules, so mock and live render identically.
