---
title: KrixGPT
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 6.31.0
python_version: "3.11"
app_file: app.py
pinned: false
---

# KrixGPT

## Authentication (Google sign-in via Firebase)

Users sign in with Google through the Firebase JS SDK in the browser. The
browser posts the Firebase ID token to `POST /auth/login`; the Python backend
verifies it and sets a signed, HttpOnly `krix_session` cookie. All chat,
history, upload and conversation routes require that cookie, and every
conversation is scoped to the signed-in user.

Firebase console setup (one time):

1. Authentication -> Sign-in method -> enable **Google**.
2. Authentication -> Settings -> **Authorized domains**: add `localhost` and any
   domain you deploy to.

Environment variables (`.env`):

| Variable | Required | Purpose |
|---|---|---|
| `SESSION_SECRET` | recommended | Signs the session cookie. If unset a random secret is used and all users are logged out on every restart. |
| `FIREBASE_PROJECT_ID` | yes | Your Firebase project id. The other `FIREBASE_*` web-config values (`FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_MESSAGING_SENDER_ID`, `FIREBASE_APP_ID`, `FIREBASE_MEASUREMENT_ID`) are read from the environment too; without `FIREBASE_API_KEY` sign-in is disabled and the app runs in guest mode. |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Path to a Firebase service-account JSON. When set, tokens are verified with the Admin SDK and revoked tokens are rejected. Without it, tokens are verified against Google's public certificates. |

Endpoints: `POST /auth/login {idToken}`, `POST /auth/logout`, `GET /auth/me`.

## Deploy for free on Hugging Face Spaces

On Hugging Face's current free plan, Docker Spaces and CPU-basic Gradio Spaces
need a PRO subscription; the free hardware for app Spaces is **ZeroGPU**. KrixGPT
needs no GPU (every model is an API call), so `app.py` declares a never-called
`@spaces.GPU` placeholder to satisfy the tier, mounts a tiny Gradio page at
`/gradio`, and keeps serving the real UI at `/`. The README header above tells
Spaces how to run it (Python 3.11, `app.py`, port 7860).

1. Create a Space at https://huggingface.co/new-space: SDK **Gradio**, template
   **Blank**, hardware **ZeroGPU** (free), visibility Public. Or from Python:
   `HfApi().create_repo("<user>/<space>", repo_type="space", space_sdk="gradio", space_hardware="zero-a10g")`.
2. Push this repo to the Space:
   `git remote add space https://huggingface.co/spaces/<user>/<space>` then
   `git push space main` (username + a *write* access token as the password).
3. Space **Settings -> Variables and secrets**. Secrets: `GROQ_API_KEY`,
   `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `SESSION_SECRET`, and the seven
   `FIREBASE_*` values from `.env`; optional `LANGSMITH_API_KEY`.
   Variables: `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=KrixGPT`.
4. Firebase Console -> Authentication -> Settings -> Authorized domains: add
   `<user>-<space>.hf.space`.
5. Open `https://<user>-<space>.hf.space` (the direct URL; the huggingface.co
   page embeds the app in an iframe that blocks the sign-in popup).

Notes: the free tier's disk resets on rebuild, so chats, memories and uploads
persist between visits but not across redeploys. Guests can chat without
signing in. The `Dockerfile` still works for paid Docker Spaces or any other
container host.

## Run locally with Docker

```bash
docker compose up --build
```

Then open http://localhost:8080. The container reads keys from `.env` and keeps
`data/`, `chroma_db/` and `uploads/` on your disk, so chats and memory survive
restarts. Stop with `Ctrl+C` or `docker compose down`.
