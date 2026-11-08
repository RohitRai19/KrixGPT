---
title: KrixGPT
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
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
| `FIREBASE_PROJECT_ID` | no | Defaults to `raiyronai`. Other `FIREBASE_*` web-config values can be overridden the same way (see `FIREBASE_WEB_CONFIG` in `app.py`). |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Path to a Firebase service-account JSON. When set, tokens are verified with the Admin SDK and revoked tokens are rejected. Without it, tokens are verified against Google's public certificates. |

Endpoints: `POST /auth/login {idToken}`, `POST /auth/logout`, `GET /auth/me`.

## Deploy for free on Hugging Face Spaces

The repo already contains a `Dockerfile` and the Space header at the top of this
README, so a Space can be created straight from GitHub.

1. Create a Space at https://huggingface.co/new-space: pick **Docker** as the SDK,
   **Blank** template, **CPU basic (free)** hardware, Public visibility.
2. Connect it to this GitHub repository (Space settings -> "Sync with GitHub"),
   or push this repo to the Space's git remote:
   `git remote add space https://huggingface.co/spaces/<user>/<space>` then
   `git push space main`.
3. In the Space settings, under **Variables and secrets**, add these **secrets**:
   `GROQ_API_KEY`, `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `SESSION_SECRET`
   (any long random string), `LANGSMITH_API_KEY` (optional).
   Add these as plain **variables**: `LANGSMITH_TRACING=true`,
   `LANGSMITH_PROJECT=KrixGPT`, and the `FIREBASE_*` values from `app.py`.
4. In Firebase Console -> Authentication -> Settings -> Authorized domains, add
   `<user>-<space>.hf.space` so Google sign-in works on the live site.
5. Open the app at `https://<user>-<space>.hf.space` (the direct URL, not the
   huggingface.co page, which embeds the app in an iframe that blocks the
   sign-in popup).

Notes: the free tier's disk is reset whenever the Space is rebuilt, so chats,
memories and uploads persist between visits but not across redeploys. Guests can
use the app without signing in.
