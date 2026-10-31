"""Print every snapshot a Site broadcasts. The same stream a dashboard socket receives.

    cd durable && PYTHONPATH=. uv run python scripts/watch_site.py [site-id] [max-messages]
"""
import sys

from generated import actors

site_id = sys.argv[1] if len(sys.argv) > 1 else "demo-warehouse"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

site = actors.Site.get(site_id)
with site.connect(actors.Site.Metadata(name="watch_site.py")) as socket:
    for n, message in enumerate(socket, 1):
        snap = getattr(message, "snapshot", None)
        if snap is None:
            print("other message:", message, flush=True)
        else:
            print(
                f"[{message.reason}] v{snap.version} total={snap.total} score={snap.compliance_score} "
                f"open={snap.open_alerts_count} by_camera={snap.by_camera} "
                f"cameras={ {k: v.online for k, v in snap.cameras.items()} } event={message.event_id}",
                flush=True,
            )
        if limit and n >= limit:
            break
