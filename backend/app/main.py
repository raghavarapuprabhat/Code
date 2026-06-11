"""FastAPI entry point for the AI Agent Platform."""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    ado_dev,
    agents,
    chat,
    code_doc,
    conversations,
    dashboards,
    health,
    sre,
    sre_fixer,
)
from app.services.scheduler import shutdown_scheduler, start_scheduler


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the schema for the zero-config in-memory SQLite default (no-op on
    # Postgres, which is seeded by infra/seed/001_init.sql in its container).
    from shared.storage import init_db

    await init_db()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router, prefix="/agents", tags=["chat"])
    app.include_router(agents.router, prefix="/agents", tags=["agents"])
    app.include_router(code_doc.router, prefix="/agents/code_doc", tags=["code_doc"])
    app.include_router(sre.router, prefix="/agents/sre", tags=["sre"])
    app.include_router(sre_fixer.router, prefix="/agents/sre_fixer", tags=["sre_fixer"])
    app.include_router(dashboards.router, prefix="/dashboards", tags=["dashboards"])
    app.include_router(ado_dev.router, prefix="/agents/ado_dev", tags=["ado_dev"])
    app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])

    return app


app = create_app()
