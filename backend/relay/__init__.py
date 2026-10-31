"""saesr relay: the agent chain (triage, coach, fixer, verifier, reporter) the camera actors call.

Protocol: durable/README.md "Relay protocol". The actor POSTs /relay/start once per step; the relay
answers 2xx at once, does the work, and reports back with ActorMessages to
POST {BACKEND_URL}/internal/cameras/{camera_id}/message.
"""
