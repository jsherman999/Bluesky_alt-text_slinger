# Alt Text Slinger

Alt Text Slinger scans a Bluesky account for image posts, generates alt text suggestions, and manages a rate-limit-aware apply queue for pushing alt text updates.

## Current behavior summary

- Scan images from the authenticated account feed.
- Cache generated alt text locally in SQLite for reuse.
- Show per-image and per-post status in the UI.
- Queue apply operations with pause/resume, rate-limit handling, and propagation state.
- Compare PDS vs public Bluesky views via a debug endpoint.

## Architecture

- Backend: FastAPI (`backend/main.py`)
- Frontend: React + Vite (`frontend/`)
- DB: SQLite (`backend/alttext_slinger.db` by default)
- Launch control (macOS): `launchd/control.sh`

## Requirements

- Python 3.11+
- Node 18+
- Bluesky app password
- Optional alt-generation key:
  - `OPENAI_API_KEY`, or
  - `OPENROUTER_API_KEY`

## Environment variables

- `OPENAI_API_KEY`: OpenAI API key for alt generation.
- `OPENROUTER_API_KEY`: OpenRouter key (OpenAI-compatible API).
- `ALTGEN_MODEL`: Model override.
  - default with OpenAI: `gpt-4o-mini`
  - default with OpenRouter: `openrouter/free`
- `ALTGEN_BASE_URL`: Optional OpenAI-compatible base URL (OpenRouter defaults to `https://openrouter.ai/api/v1`).
- `ALTGEN_MAX_TOKENS`: Max tokens for alt generation (default `80`).
- `ALTGEN_HTTP_REFERER`: Optional header for OpenRouter.
- `ALTGEN_X_TITLE`: Optional header/title (default `Bluesky Alt-Text Slinger`).
- `ALTTS_DB_PATH`: SQLite DB path override.

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Frontend defaults API base to `http://<current-host>:8000`.

## Launchd control (macOS)

```bash
./launchd/control.sh install
./launchd/control.sh start
./launchd/control.sh status
./launchd/control.sh logs
./launchd/control.sh restart
./launchd/control.sh stop
```

## Key API endpoints

- `POST /api/scan/stream`: streaming scan progress + final result.
- `POST /api/generate/start`: start background generation job.
- `GET /api/generate/events/{job_id}`: poll generation events.
- `POST /api/generate/stop/{job_id}`: stop generation job.
- `POST /api/generate/one`: regenerate one image alt.
- `POST /api/apply/queue/start`: start apply queue.
- `POST /api/apply/queue/pause/{job_id}`: pause apply queue.
- `POST /api/apply/queue/resume/{job_id}`: resume apply queue.
- `GET /api/apply/queue/state/{job_id}`: queue status and per-item states.
- `GET /api/debug/alt-compare?uri=at://...`: compare PDS/public alt views for a post.

## Additional docs

- `docs/PDS_INSPECT_SCRIPT.md`: CLI script to inspect DID/PDS records and watch convergence.
- `docs/PROPAGATION_BEHAVIOR.md`: explanation of PDS/public propagation mismatch and operational guidance.

## Notes

- Use a Bluesky app password, not your account password.
- Apply queue can show `propagating` when PDS accepted writes but public appview has not converged.
- Local DB state is advisory; public verification is required for end-user-visible success.
