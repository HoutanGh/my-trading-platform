import os
from typing import List

from dotenv import load_dotenv

load_dotenv()

# Comma-separated list of allowed origins for CORS. Falls back to Vite dev server.
_origins_env = os.getenv("FRONTEND_ORIGINS") or os.getenv("FRONTEND_ORIGIN")
if _origins_env:
    CORS_ORIGINS: List[str] = [origin.strip() for origin in _origins_env.split(",") if origin.strip()]
else:
    CORS_ORIGINS = ["http://localhost:5173"]

# Optional default account for local testing; not enforced by the API.
DEFAULT_ACCOUNT = os.getenv("DEFAULT_ACCOUNT")
