"""
Firebase authentication for the FastAPI backend.

Flow:
  1. The browser signs in with Google through the Firebase JS SDK.
  2. The browser POSTs the Firebase ID token to /auth/login.
  3. We verify the ID token with the Firebase Admin SDK and issue our own
     signed, HttpOnly session cookie (no Firebase credentials file needed
     for verification, only the project ID).
  4. Protected routes read the cookie through `get_current_user`.
"""

import os
import secrets
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")

SESSION_COOKIE_NAME = "krix_session"
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(14 * 24 * 60 * 60)))

_session_secret = os.getenv("SESSION_SECRET")
if not _session_secret:
    # Random per-process secret: sessions are invalidated on restart.
    # Set SESSION_SECRET in .env for persistent sessions.
    _session_secret = secrets.token_urlsafe(48)
    print("WARNING: SESSION_SECRET not set; using a random secret (sessions reset on restart).")

_serializer = URLSafeTimedSerializer(_session_secret, salt="krix-session")


# True when a service-account file is configured and the Admin SDK can be used
# (enables revocation checks). Otherwise tokens are verified with google-auth,
# which only needs Google's public certificates and the project ID.
USE_ADMIN_SDK = False


def _init_firebase():
    global USE_ADMIN_SDK

    if firebase_admin._apps:
        USE_ADMIN_SDK = True
        return

    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("FIREBASE_SERVICE_ACCOUNT")

    if cred_path and os.path.exists(cred_path):
        from firebase_admin import credentials
        firebase_admin.initialize_app(
            credentials.Certificate(cred_path),
            {"projectId": FIREBASE_PROJECT_ID},
        )
        USE_ADMIN_SDK = True


_init_firebase()

_google_request = google_requests.Request()


def _decode_token(id_token: str) -> dict:
    if USE_ADMIN_SDK:
        try:
            return firebase_auth.verify_id_token(id_token, check_revoked=True)
        except firebase_auth.ExpiredIdTokenError:
            raise HTTPException(status_code=401, detail="Login token expired. Please sign in again.")
        except firebase_auth.RevokedIdTokenError:
            raise HTTPException(status_code=401, detail="Login token revoked. Please sign in again.")
        except (firebase_auth.InvalidIdTokenError, ValueError) as e:
            raise HTTPException(status_code=401, detail=f"Invalid login token: {e}")

    try:
        return google_id_token.verify_firebase_token(
            id_token,
            _google_request,
            audience=FIREBASE_PROJECT_ID,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid login token: {e}")


def verify_firebase_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return a user dict."""
    decoded = _decode_token(id_token)

    uid = decoded.get("uid") or decoded.get("sub") or decoded.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid login token: no user id.")

    return {
        "uid": uid,
        "email": decoded.get("email"),
        "name": decoded.get("name"),
        "picture": decoded.get("picture"),
    }


def create_session_token(user: dict) -> str:
    return _serializer.dumps(
        {
            "uid": user["uid"],
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
        }
    )


def read_session_token(token: str) -> Optional[dict]:
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def get_optional_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return read_session_token(token)


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: require a logged-in user."""
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user
