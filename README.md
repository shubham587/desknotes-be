---
title: DeskNotes Backend
emoji: 📝
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# DeskNotes — backend

FastAPI backend for DeskNotes: photo-to-notes transcription, notes/folders/tags,
todos and checklists. Data lives in Postgres (Supabase); uploaded media is on disk.

## Run locally
```bash
uv sync
cp .env.example .env   # fill in DATABASE_URL, OPENAI_API_KEY, DESKNOTES_SECRET
uv run uvicorn app.main:app --reload --port 8000
```

## Environment
See `.env.example`. Required: `DATABASE_URL` (Supabase session pooler),
`DESKNOTES_SECRET` (JWT key). Set `ALLOWED_ORIGINS` to the frontend URL in prod.

Deploys as a Docker container (Hugging Face Spaces) or via the `Procfile`
(buildpack hosts). The `sdk: docker` / `app_port` header above is read by
Hugging Face Spaces.
