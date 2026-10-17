
"""Central configuration for paths, limits, and runtime settings."""

import os
from pathlib import Path

# Locate the repository and its shared data directory.
REPO_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_DIR / "data"

# Use the environment value when set; otherwise use local development defaults.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://relief:relief@localhost:5432/relief",
)

# HMAC key used to pseudonymize sender IDs. Replace the default outside development.
SENDER_HASH_SECRET = os.environ.get(
    "SENDER_HASH_SECRET", "dev-only-secret"
).encode()

# Empty means no Telegram bot token is configured.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Password for the restricted application database role.
# The migration script creates or configures this role when needed.
APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD", "")

# JWT signing key. Do not use the development default in production.
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-jwt-secret")

# Authentication tokens expire after 60 minutes.
TOKEN_TTL_MIN = 60
H3_RESOLUTION = 8  # must match the resolution-8 cells in 001_base.sql
