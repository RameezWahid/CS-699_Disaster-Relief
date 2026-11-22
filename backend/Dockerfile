# Build context is the repo root (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Mirror the repo layout so config.REPO_DIR resolves to /app:
# /app/backend/scripts, /app/db/migrations, and /app/data (mounted by compose).
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY db/migrations /app/db/migrations
COPY backend/scripts ./scripts

# Do not run as root. UID 1000 matches the usual host user, so files written to the
# mounted ./data directory (report photos) stay owned by that user.
RUN useradd --uid 1000 --create-home relief
USER relief
