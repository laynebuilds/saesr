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

- "Try the demo" buttons open `/demo/`: the dashboard in mock mode, built from `dashboard/` with `cd dashboard && DEMO_BASE=/demo/ VITE_MOCK=1 npx vite build --outDir ../landing/demo --emptyOutDir`, then re-add the footage (gitignored in dashboard/public/footage) to `landing/demo/footage`. `vercel.json` rewrites `/demo/*` to the app so deep links work.
- "View on GitHub" in the closing section links to github.com/laynebuilds/saesr.
- Product screenshots in `assets/shots` were captured from the saesr dashboard on Oct 31 2026.
- Footage and stills are Pexels-licensed; see `assets/CREDITS.md`.
