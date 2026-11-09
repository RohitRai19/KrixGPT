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

## Deploy on AWS Free Tier (EC2 + Docker)

The app ships as a Docker image (see `Dockerfile`). The AWS Free Tier gives a
small EC2 instance free for 12 months, which is enough for a demo.

### 1. Launch the instance
- EC2 -> Launch instance. AMI: **Ubuntu Server 24.04 LTS**. Type: **t3.micro**
  (or t2.micro) - the Free Tier eligible size. Create/download a key pair.
- Storage: 20 GB gp3 (Free Tier allows up to 30 GB).
- Security group inbound rules: SSH 22 (your IP), HTTP 80 and HTTPS 443 (anywhere).
- Note the public IPv4 address after launch. Allocate an **Elastic IP** and
  associate it so the address survives restarts.

### 2. Point a free domain at it
Google sign-in requires HTTPS, and HTTPS requires a hostname. Create a free
subdomain at https://www.duckdns.org (e.g. `krixgpt.duckdns.org`) and set its IP
to the Elastic IP.

### 3. Install Docker on the instance
```bash
ssh -i your-key.pem ubuntu@<elastic-ip>
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker
# 1 GB of RAM is tight for ChromaDB + LangChain: add swap so the app never OOMs
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 4. Get the code and secrets onto the server
```bash
git clone https://github.com/RohitRai19/KrixGPT.git && cd KrixGPT
nano .env        # paste the same keys as your local .env (never commit this file)
```
Add one more line to `.env` with your domain, used by the HTTPS proxy:
```
DOMAIN=krixgpt.duckdns.org
```

### 5. Start it with HTTPS
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
`docker-compose.prod.yml` adds a Caddy reverse proxy that obtains and renews a
free Let's Encrypt certificate for `DOMAIN` automatically and forwards traffic
to the app. First start takes a few minutes (image build + certificate).

### 6. Allow Google sign-in from the new domain
Firebase Console -> Authentication -> Settings -> Authorized domains -> add
`krixgpt.duckdns.org`. Then open `https://krixgpt.duckdns.org`.

### Updating
```bash
cd ~/KrixGPT && git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Notes: data (SQLite, Chroma index, uploads) lives in bind-mounted folders on the
instance disk and survives restarts and redeploys. Free Tier covers 750 hours a
month of one micro instance for 12 months; after that a t3.micro is roughly
$8/month, or stop the instance when not demoing.

## Run locally with Docker

```bash
docker compose up --build
```

Then open http://localhost:8080. The container reads keys from `.env` and keeps
`data/`, `chroma_db/` and `uploads/` on your disk, so chats and memory survive
restarts. Stop with `Ctrl+C` or `docker compose down`.
