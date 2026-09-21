"""Runtime config from env. Sensible dev defaults; override in production.

Loads backend/.env if present (gitignored, holds secrets like HF_TOKEN).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DATA_DIR = Path(os.getenv("DESKNOTES_DATA_DIR", Path(__file__).parent.parent / "data"))
MEDIA_DIR = DATA_DIR / "media"

# Postgres (Supabase). Use the Session-pooler URI. Required in production;
# without it the app can't reach the database.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Single seeded account (Phase 0 — no signup).
SEED_USER = os.getenv("DESKNOTES_USER", "admin")
SEED_PASSWORD = os.getenv("DESKNOTES_PASSWORD", "admin")

# ponytail: dev default secret; MUST set DESKNOTES_SECRET in prod.
JWT_SECRET = os.getenv("DESKNOTES_SECRET", "dev-insecure-change-me")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 24 * 30  # long-lived; it's a personal single-user app

# Transcription providers. Precedence: OpenAI > Hugging Face > offline stub.
# OpenAI (preferred): set OPENAI_API_KEY. Vision model, one call -> structured JSON.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("DESKNOTES_OPENAI_MODEL", "gpt-4o")

# Hugging Face fallback — set token to enable if no OpenAI key.
HF_TOKEN = os.getenv("HF_TOKEN", "")
# Auto-router picks whichever provider your token has enabled. The 7B isn't
# served by any provider; the 72B is. Override with DESKNOTES_HF_MODEL.
HF_MODEL = os.getenv("DESKNOTES_HF_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct")

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
