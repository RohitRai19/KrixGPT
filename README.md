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
