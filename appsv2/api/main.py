from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from appsv2.adapters.pnl.db import ensure_schema
from appsv2.api import settings
from appsv2.api.routes import pnl


def create_app() -> FastAPI:
    app = FastAPI(title="my-trading-platform API")

    # Allow calls from the frontend dev server (and any configured origins).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        # Ensure the daily_pnl table exists before serving requests.
        ensure_schema()

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(pnl.router)
    return app


app = create_app()
