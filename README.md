# Bluesky Alt-Text Slinger

A Linux-friendly web app that connects to your Bluesky account, scans all posts with images, generates suggested alt text, and lets you review + apply alt text updates back to Bluesky.

- Backend: FastAPI + `atproto` + OpenAI
- Frontend: React (Vite + TypeScript)
- Storage: SQLite
- Container: Docker (backend)

> **Important:** Use a **Bluesky app password**, not your main password.

---

## Features

- Login with Bluesky handle + app password
- Scan all posts with `app.bsky.embed.images`
- Show:
  - Thumbnails
  - Existing alt text
  - Suggested alt text (via OpenAI Vision)
- Edit **alt text to apply** per image
- Select which images to update and apply in bulk
- Filters:
  - All images
  - Only images missing alt
  - Only images with existing alt
  - Only selected images
- Bulk actions:
  - Select all missing-alt images
  - Clear all selections
- SQLite database for caching scans and applied updates

---

## Prerequisites

- Python 3.11+ (for backend if not using Docker)
- Node 18+ (for frontend)
- A Bluesky account with an **app password**
- An OpenAI API key (for alt-text generation)

---

## Backend (dev)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

pip install -r requirements.txt

export OPENAI_API_KEY="sk-..."           # required for suggestions
export ALTTS_DB_PATH="./alttext_slinger.db"  # optional; defaults to backend-local file

uvicorn backend.main:app --reload --port 8000

The API will be available at http://localhost:8000.

⸻

Frontend (dev)

cd frontend
npm install
npm run dev

By default, Vite serves at http://localhost:5173.

The frontend expects the backend at http://localhost:8000. You can change that in src/api.ts if needed.

⸻

Docker

Build the backend image:

docker build -t bluesky-alttext-slinger .

Run:

docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ALTTS_DB_PATH="/app/data/alttext_slinger.db" \
  -v "$(pwd)/data:/app/data" \
  bluesky-alttext-slinger

Then run the frontend separately (see above) and point it to http://localhost:8000.

⸻

Environment variables
	•	OPENAI_API_KEY – OpenAI API key (required for alt-text generation).
	•	ALTTS_DB_PATH – Path to SQLite DB (optional; defaults under backend/ or /app/data in Docker).
	•	ALTGEN_MODEL – Optional; defaults to gpt-4o-mini.

⸻

Notes
	•	No alt text is changed on Bluesky until you click "Apply Selected Alt Text".
	•	You can revoke the app password anytime from Bluesky settings.
	•	This project is not affiliated with Bluesky or OpenAI; it's just a client built on their APIs.

⸻



---