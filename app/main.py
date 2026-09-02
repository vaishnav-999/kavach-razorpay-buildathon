"""Kavach application factory.

M0 wires configuration validation at startup, /health, the seed and the static
mount. M2 adds the §7 merchant plane. M3 adds §12 payments. M4 adds the §12.5
webhook. M5 adds the §8 Mandate Authority and, with it, the Transaction Guard
behind `POST /merchant/checkout/submit`. Later routers arrive with their
milestones.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import SessionLocal
from app.errors import install_exception_handlers
from app.merchant.router import public_router as merchant_public_router
from app.merchant.router import router as merchant_router
from app.merchant.seed import seed_database
from app.platform import dev as dev_scaffold
from app.platform.mandate import router as mandate_router
from app.platform.payments import router as payments_router
from app.platform.webhooks import router as webhooks_router
from app.schemas import HealthOut

STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("kavach")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Configuration has already been validated at import time (§4). Reaching
    # here means the environment is fit to serve.
    db = SessionLocal()
    try:
        if seed_database(db):
            log.info("seed: §16 merchants and products inserted")
        else:
            log.info("seed: merchants already present, skipped")
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kavach",
        description=(
            "An AI agent that can transact for real while being structurally "
            "incapable of exceeding the authority a user granted it."
        ),
        version="1.0",
        lifespan=lifespan,
    )

    install_exception_handlers(app)

    # §18.3 — every endpoint accepts and echoes X-Request-Id, generating one
    # when absent.
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/health", response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut(status="ok")

    # §7 — the merchant plane. Registered before the static mount, because
    # Starlette matches routes in the order they were added and the mount at
    # "/" would otherwise swallow everything.
    app.include_router(merchant_public_router)
    app.include_router(merchant_router)

    # §12.4 and §12.6 — payment verification and reconciliation.
    app.include_router(payments_router)

    # §12.5 — the Razorpay webhook. Razorpay is the only caller in
    # production; the §15 replay demo POSTs a stored body back to it.
    app.include_router(webhooks_router)

    # §8 — the Mandate Authority. Propose grants nothing; only issue signs.
    app.include_router(mandate_router)

    # §12.3 — the checkout page. The M3 scaffolding that created orders without
    # a guard decision is gone; what remains is the page that pays an order the
    # Guard already authorised. M7 replaces it with the §14 frontend.
    app.include_router(dev_scaffold.page_router)

    # The built frontend lands here (M7). The directory is gitignored, so make
    # sure it exists before mounting.
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
