# Perspicacité POC — CNRS chart frontend

Proof-of-concept rebuild of the Perspicacité GUI using:

- Next.js 16 + React 19 + TypeScript
- Tailwind CSS v4 (via `@theme inline` tokens in `src/app/globals.css`)
- IBM Plex Sans / Mono (CNRS charte fonts, OFL)
- CNRS official palette and halo signature device
- Bloc-marque État–CNRS in the footer

Backend is untouched: the dev proxy in `next.config.ts` forwards
`/api/*` to the existing FastAPI server at `http://localhost:8000`.

## Dev loop

```bash
# 1. Start the Perspicacité backend (8000) — same as today.
cd ../../../../ && uv run perspicacite serve

# 2. In another terminal, start the POC frontend.
cd frontend
npm install
npm run dev   # http://localhost:3000
```

Override the backend URL by setting `PERSPICACITE_BACKEND_URL` in the
shell that runs `npm run dev`.

## What this POC demonstrates

- Streaming chat against `POST /api/chat` (stream=true), with cancel
  via `POST /api/chat/cancel`
- The six documented RAG modes as a selectable card grid
- CNRS chart applied end-to-end (halo, palette, typography, logos)
- Same-origin requests via Next.js rewrites — no CORS work needed
  on the backend
