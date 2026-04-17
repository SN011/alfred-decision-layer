# Deploying to Render

This repo is configured for one-click deploy to [Render](https://render.com) via `render.yaml` (Blueprint).

## Steps

1. **Fork or own this repo on GitHub.**

2. **Sign in to Render** at [render.com](https://render.com) (free tier is fine for this prototype).

3. **New → Blueprint.**
   - Connect your GitHub account if you haven't.
   - Select the `alfred-decision-layer` repo.
   - Render auto-detects `render.yaml` and shows the service it will create.
   - Click **Apply**.

4. **Add your API key.**
   After the service is created, go to **Environment** in the Render dashboard and set:
   - `XAI_API_KEY` = your xAI key (preferred — uses `grok-4-1-fast-reasoning`)
   - OR `OPENAI_API_KEY` = your OpenAI key (falls back to `gpt-4o-mini`)
   - OR `GROQ_API_KEY` = your Groq key (falls back to `llama-3.3-70b-versatile`)

   Only one is required. The server picks xAI → OpenAI → Groq in that order.

5. **Deploy.** Render will build + start the service. First cold start takes ~60s on the free tier. Live URL appears in the dashboard.

## What `render.yaml` does

```yaml
services:
  - type: web
    name: alfred-decision-layer
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn server:app --host 0.0.0.0 --port $PORT
```

- Python 3.12
- Installs all dependencies from `requirements.txt`
- Starts FastAPI via `uvicorn` on Render's `$PORT`
- Serves the frontend from `/` and the API from `/chat`, `/api/*`, `/health`

## Notes

- **Free tier sleeps after inactivity.** First request after ~15 min of idle will take ~30s to cold start. Fine for a demo.
- **SQLite memory is ephemeral on free tier.** Restarts wipe the `tmp/alfred_decisions.db`. For persistent memory, upgrade to a paid plan and attach a Render Disk mounted at `/opt/render/project/src/tmp`.
- **No Docker required** — Render builds from `requirements.txt` directly. The bundled `Dockerfile` is optional for other platforms.

## After deploy

- Hit `/health` to confirm the model is wired up.
- Hit `/` to use the UI.
- Hit `/docs` for the FastAPI auto-generated API docs.
