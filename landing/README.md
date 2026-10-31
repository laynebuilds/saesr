# saesr landing page (website)

Lives in `landing/` of the saesr repo.

Static marketing site for saesr: plain HTML, CSS and JS, no build step.

## Run locally

```bash
cd landing && python3 -m http.server 8795
# open http://localhost:8795
```

## Deploy on your domain

Any static host works. On Vercel: import this repo, framework preset "Other", no build command, root directory `landing`. Then add the domain under Project > Settings > Domains.

## Notes

- "Book a demo" buttons email layne0906@icloud.com (search `mailto:` in index.html to change).
- "Open the live dashboard" in the closing section points to `http://localhost:5173`. Change it to the deployed dashboard URL before going live.
- Product screenshots in `assets/shots` were captured from the saesr dashboard on Oct 31 2026.
- Footage and stills are Pexels-licensed; see `assets/CREDITS.md`.
