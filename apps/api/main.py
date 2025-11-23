from fastapi import FastAPI

from apps.api.routes import pnl
from apps.data.db import ensure_schema


def create_app() -> FastAPI:
    app = FastAPI(title="Trading Platform API")

    @app.on_event("startup")
    def _startup() -> None:
        # Ensure the daily_pnl table exists before serving requests.
        ensure_schema()

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Register route groups
    app.include_router(pnl.router)

    return app


app = create_app()
