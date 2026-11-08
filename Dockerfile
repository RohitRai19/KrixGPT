# KrixGPT on Hugging Face Spaces (Docker SDK) or any container host.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

# Spaces run the container as a non-root user with uid 1000.
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Writable folders for SQLite, LangGraph checkpoints, Chroma and uploads.
RUN mkdir -p data chroma_db uploads && chown -R appuser:appuser /app

USER appuser

EXPOSE 7860

# $PORT is 7860 on Spaces; other hosts (Render, Railway, Cloud Run) inject their own.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
