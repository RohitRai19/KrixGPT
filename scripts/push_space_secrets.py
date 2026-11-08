"""
Copy the app's secrets from .env to a Hugging Face Space.

Usage (from the project root, venv active):
    python scripts/push_space_secrets.py Rohitrai19/krixgpt

Needs a Hugging Face *write* token, read from (first match wins):
    HF_TOKEN env var  ->  HF_TOKEN in .env  ->  `hf auth login` cache

Secret values are never printed.
"""

import os
import sys

from dotenv import dotenv_values
from huggingface_hub import HfApi

SECRETS = [
    "GROQ_API_KEY",
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "SESSION_SECRET",
    "LANGSMITH_API_KEY",
    "XAI_API_KEY",
    "FIREBASE_API_KEY",
    "FIREBASE_AUTH_DOMAIN",
    "FIREBASE_PROJECT_ID",
    "FIREBASE_STORAGE_BUCKET",
    "FIREBASE_MESSAGING_SENDER_ID",
    "FIREBASE_APP_ID",
    "FIREBASE_MEASUREMENT_ID",
]

VARIABLES = {
    "LANGSMITH_TRACING": "true",
    "LANGSMITH_PROJECT": "KrixGPT",
    "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
}


def main() -> int:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        print(__doc__)
        return 2

    repo_id = sys.argv[1]
    env = dotenv_values(".env")
    token = os.getenv("HF_TOKEN") or env.get("HF_TOKEN")
    api = HfApi(token=token)  # falls back to the cached login if token is None

    try:
        who = api.whoami()
    except Exception as exc:
        print(f"Not logged in to Hugging Face: {type(exc).__name__}. "
              "Set HF_TOKEN in .env (write token) or run `hf auth login`.")
        return 1
    print(f"Authenticated as {who.get('name')}; target Space: {repo_id}")

    pushed, skipped = [], []
    for name in SECRETS:
        value = (env.get(name) or "").strip()
        if not value:
            skipped.append(name)
            continue
        api.add_space_secret(repo_id=repo_id, key=name, value=value)
        pushed.append(name)

    for name, value in VARIABLES.items():
        api.add_space_variable(repo_id=repo_id, key=name, value=env.get(name) or value)

    print("Secrets set   :", ", ".join(pushed))
    print("Variables set :", ", ".join(VARIABLES))
    if skipped:
        print("Skipped (empty in .env):", ", ".join(skipped))
    print("The Space restarts automatically to pick up the new values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
