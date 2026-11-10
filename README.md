# KrixGPT

An agentic AI assistant that decides when to use tools instead of answering only
from the model's memory: live web search, question answering over uploaded
documents, long-term memory about the user, live weather and stock data, and an
exact calculator. It runs on multiple LLM providers with automatic failover,
works without an account, and supports Google sign-in to keep chats across devices.

**Live demo:** https://krixgpt.onrender.com (free tier; the first request after an
idle period can take up to a minute while the instance wakes).

## Features

- **Tool-using agent** built as a LangGraph state machine: the model chooses
  between answering and calling a tool, tool results flow back into the loop,
  and every step is checkpointed so conversations are resumable.
- **Tools:** Tavily web search, document search over uploaded PDF/DOCX/TXT/MD/CSV
  files (RAG with ChromaDB), save and recall user memory, weather (Open-Meteo),
  stock quotes (Yahoo Finance), and a sandboxed calculator.
- **Multiple LLM providers:** Groq (GPT-OSS 120B/20B, Qwen 3.8 27B) and Google
  Gemini (3.x Flash/Pro), with xAI Grok pre-wired. Keys are validated at startup,
  providers without a working key are hidden, and a failed request is retried on
  the next model and then the next provider before the user sees an error.
- **Guest-first access with optional Google sign-in** (Firebase). Guests get a
  per-browser identity; signing in migrates their chats and memory to the
  account. Conversation ownership is enforced server-side.
- **Streaming UI:** tokens stream over Server-Sent Events, Markdown rendering,
  auto-generated chat titles, pin/rename/delete per chat, typed prompt
  suggestions, voice input, dark/light theme, responsive layout.
- **Observability:** every run is traced in LangSmith when a key is configured.

## Architecture

```
Browser (HTML/CSS/JS, SSE reader)
   |  POST /chat/stream {message, thread_id, model, guest_id}
   v
FastAPI (app.py) ── auth.py: Firebase ID-token verification, signed HttpOnly session cookie
   |  stores message, enforces ownership, sets thread/owner context for tools
   v
LangGraph agent (agent.py): START -> chatbot -> tools_condition -> tools -> chatbot ...
   |-- providers.py: model registry, key validation, failover chain
   |-- tools.py / extra_tools.py: web search, calculator, RAG, memory, weather, stocks
   |-- SqliteSaver checkpointer keyed by thread_id
   v
SSE events: {token}... {title} {done} | {error}
```

Storage: SQLite via SQLAlchemy (conversations, messages, long-term memory with an
owner id), a second SQLite file for LangGraph checkpoints, and a ChromaDB index on
disk for document chunks (embedded with `gemini-embedding-001`).

## Tech stack

Python 3.11, FastAPI, Uvicorn, LangChain 1.x, LangGraph 1.x, langchain-openai
(Groq/xAI via OpenAI-compatible APIs), langchain-google-genai, ChromaDB,
SQLAlchemy, Firebase Authentication, itsdangerous, Tavily, yfinance, Jinja2,
Docker. Exact versions are pinned in `requirements.txt`.

## Project layout

| File | Purpose |
|---|---|
| `app.py` | FastAPI app: routes for chat streaming, history, uploads, conversation actions, auth |
| `agent.py` | LangGraph graph, system prompt, per-model agent cache, checkpoint cleanup |
| `providers.py` | Model/provider registry, key validation, default model, fallback chain |
| `tools.py`, `extra_tools.py` | Tool definitions (search, calculator, RAG, memory, weather, stocks) |
| `rag.py` | Document ingestion, chunking, embeddings, retrieval |
| `database.py` | SQLAlchemy models, migrations, conversation/message/memory queries |
| `auth.py` | Firebase token verification and session cookie handling |
| `titles.py` | Conversation title generation with a cheap model and a heuristic fallback |
| `templates/index.html` | The single-page frontend |
| `Dockerfile`, `docker-compose*.yml`, `Caddyfile` | Container build, local run, HTTPS production overlay, Docker Hub overlay |

## Running locally

Requirements: Python 3.11 and API keys for at least one chat provider.

```bash
git clone https://github.com/RohitRai19/KrixGPT.git
cd KrixGPT
python -m venv .venv
.venv/Scripts/activate        # Windows (Git Bash) - use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp .env.example .env          # or create .env by hand, see below
python app.py                 # http://localhost:8080
```

Or with Docker (reads `.env`, keeps data in `data/`, `chroma_db/`, `uploads/`):

```bash
docker compose up --build
```

### Environment variables (`.env`)

Values must be unquoted (`KEY=value`) so the file works with python-dotenv,
Docker Compose and `docker run --env-file` alike. Never commit this file.

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | one of these | Groq models (default provider when present). Free tier at console.groq.com |
| `GOOGLE_API_KEY` | one of these | Gemini chat models and `gemini-embedding-001` for document search |
| `XAI_API_KEY` | no | Enables Grok models |
| `TAVILY_API_KEY` | for web search | Tavily search tool |
| `SESSION_SECRET` | recommended | Signs the login session cookie; if unset a random secret is used and logins reset on restart |
| `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_MESSAGING_SENDER_ID`, `FIREBASE_APP_ID`, `FIREBASE_MEASUREMENT_ID` | for sign-in | Firebase web app config. Without `FIREBASE_API_KEY` the app runs in guest mode only |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Firebase service-account JSON path; enables revoked-token checks |
| `DEFAULT_MODEL` | no | Force a default model id (see `providers.py`) |
| `TITLE_MODEL`, `GENERATE_TITLES` | no | Model for chat titles; set `GENERATE_TITLES=false` to use the heuristic only |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | no | LangSmith tracing |
| `PORT` | no | Listen port (default 8080 locally, 7860 in the container) |

Document search needs `GOOGLE_API_KEY` for embeddings even when Groq handles chat.

### Firebase setup (for Google sign-in)

1. Create a Firebase project and a Web app; copy its config into the
   `FIREBASE_*` variables.
2. Authentication -> Sign-in method -> enable **Google**.
3. Authentication -> Settings -> **Authorized domains**: add `localhost` and every
   domain you deploy to.

The browser signs in with Google through the Firebase SDK and posts the ID token
to `POST /auth/login`; the server verifies it against Google's public certificates
and issues its own signed, HttpOnly `krix_session` cookie. Guests can use every
feature without signing in; their threads and memories are keyed to a browser id
and are claimed by the account on sign-in.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/chat/stream` | SSE stream of `{token}` events, then `{title}` on the first turn and `{done}` |
| `GET` | `/conversations` | Signed-in: the user's chats. Guest: `?thread_ids=a,b` for the browser's own chats |
| `PATCH` | `/conversations/{thread_id}` | `{title}` to rename, `{pinned}` to pin |
| `DELETE` | `/conversations/{thread_id}` | Removes the chat, its messages, graph state and uploaded documents |
| `GET` | `/history/{thread_id}` | Messages of a chat |
| `POST` | `/upload` | Multipart file + `thread_id`; indexes the document for that chat |
| `POST` | `/auth/login`, `/auth/logout`; `GET /auth/me` | Session management |

All conversation routes check ownership: a thread owned by another account returns 403.

## Deployment

The app ships as a Docker image (`python:3.11-slim`, non-root user, port 7860).
Published image: `rohitrai1904/krixgpt:latest`.

### Render (current live demo)

New -> Web Service -> **Existing Image** `docker.io/rohitrai1904/krixgpt:latest`
-> Free instance -> add the environment variables from the table above plus
`PORT=7860` -> Health Check Path `/` -> Deploy. Add the `onrender.com` hostname
to Firebase's authorized domains. To ship an update, push a new image and use
**Manual Deploy -> Deploy latest reference**.

Note: Render's free plan sleeps after 15 idle minutes and has no persistent disk,
so chats, memories and uploads reset on redeploy.

### Any VM with Docker (AWS, GCP, etc.)

Tested target: an AWS EC2 t3.micro (Ubuntu 24.04). New AWS accounts get a
credit pool valid for about six months instead of the old 12-month free tier;
a t3.micro plus a public IPv4 address fits comfortably inside it.

`docker-compose.prod.yml` adds a Caddy reverse proxy with automatic Let's Encrypt
HTTPS (required for Google sign-in); `docker-compose.hub.yml` pulls the published
image instead of building on the server.

```bash
# on the server, after installing Docker and cloning the repo
nano .env                                  # keys from the table above, plus DOMAIN=your.host.name
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hub.yml up -d
```

Add the domain to Firebase's authorized domains. On a 1 GB instance, add 2 GB of
swap before the first start. Data lives in bind-mounted folders on the instance
disk and survives restarts and updates. To update: `git pull`, then the same
`compose ... pull` and `up -d`.

### Publishing a new image

```bash
docker compose build
docker tag krixgpt:local rohitrai1904/krixgpt:latest
docker push rohitrai1904/krixgpt:latest
```

## Roadmap

- MongoDB order tools (typed, user-scoped queries) for product/order questions.
- Human-in-the-loop approval before side-effect actions, using LangGraph interrupts.
- Expose the tool set over the Model Context Protocol.
- Postgres for conversations and checkpoints, per-user rate limiting, evaluation
  datasets in LangSmith.
