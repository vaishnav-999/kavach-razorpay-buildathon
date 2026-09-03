"""Test harness (BUILD_SPEC §17).

Two things happen at import time, before anything from `app` is imported, and
the order matters: the environment is pinned, then a throwaway Postgres
database is created. `app.config` validates and caches its settings the moment
it is first imported, so a `DATABASE_URL` set later would never be read.

**Invariant I-11: no test makes an LLM API call. Ever.** `LLM_PROVIDER` is
`cassette` and `CASSETTE_MODE` is `replay`; `app.buyer.llm.get_provider` is
wrapped by a guard that raises the moment a test would reach for a live
provider, and `_live_provider` is replaced by one that always raises. `pytest`
costs zero tokens and always will.

Every test uses this throwaway database and injected `now` values rather than
the wall clock. The domain builders the tests construct rows with live in
`tests/factories.py`, which is imported only from a test module — by then this
file has already pinned the environment.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

# -- 1. the environment, pinned before app.config is imported --------------

_ADMIN_URL = os.environ.get(
    "KAVACH_TEST_DB_ADMIN_URL", "postgresql://kavach:kavach@localhost:5433/postgres"
)
_TEST_DB_NAME = f"kavach_test_{uuid.uuid4().hex[:10]}"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))


_TEST_DB_URL = _with_database(_ADMIN_URL, _TEST_DB_NAME)

# Fixed seeds, so a signature produced in one run is reproducible in the next.
# Distinct, because the Mandate Authority and the merchant are separate signers
# and `app/config.py` refuses to start if they are not.
os.environ.update(
    {
        "DATABASE_URL": _TEST_DB_URL,
        "APP_BASE_URL": "http://testserver",
        "DEMO_MODE": "true",
        "LOG_LEVEL": "WARNING",
        "RAZORPAY_KEY_ID": "rzp_test_kavachtests1",
        "RAZORPAY_KEY_SECRET": "kavach_test_key_secret",
        "RAZORPAY_WEBHOOK_SECRET": "kavach_test_webhook_secret",
        "MANDATE_SIGNING_SEED": "11" * 32,
        "MERCHANT_SIGNING_SEED": "22" * 32,
        "MERCHANT_API_KEY": "kavach_test_merchant_key",
        # I-11.
        "LLM_PROVIDER": "cassette",
        "CASSETTE_MODE": "replay",
        "CASSETTE_DIR": "cassettes",
    }
)


def _create_test_database() -> None:
    conn = psycopg2.connect(_ADMIN_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        conn.close()


def _drop_test_database() -> None:
    conn = psycopg2.connect(_ADMIN_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (_TEST_DB_NAME,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"')
    finally:
        conn.close()


_create_test_database()

# -- 2. only now may app be imported ---------------------------------------

from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.merchant import seed as seed_module  # noqa: E402
from app.platform import razorpay_client  # noqa: E402

import app.buyer.llm as llm_module  # noqa: E402

_TABLES = (
    "audit_events",
    "payments",
    "webhook_events",
    "orders",
    "guard_decisions",
    "quotes",
    "cart_items",
    "carts",
    "mandates",
    "agent_sessions",
    "products",
    "merchants",
)


# -- 3. database lifecycle -------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    _drop_test_database()


@pytest.fixture
def db(_schema):
    """A session on a freshly truncated, freshly seeded throwaway database."""
    session = SessionLocal()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"
        )
    seed_module.seed_database(session)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    """The real app over the real router stack, on the throwaway database.

    The lifespan context is deliberately not entered: seeding is the `db`
    fixture's job, and a startup hook re-running it would fight the truncation.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# -- 4. I-11: nothing here may reach a live model --------------------------

real_get_provider = llm_module.get_provider


def _no_live_provider(*args, **kwargs):
    raise AssertionError(
        "A test reached for a live LLM provider. Invariant I-11: no test makes "
        "an LLM API call, ever."
    )


@pytest.fixture(autouse=True)
def _no_llm_calls(monkeypatch):
    def guarded(*args, **kwargs):
        name = (settings.LLM_PROVIDER or "").strip().lower()
        mode = (settings.CASSETTE_MODE or "").strip().lower()
        if name in ("gemini", "anthropic") and mode != "replay":
            _no_live_provider()
        return real_get_provider(*args, **kwargs)

    monkeypatch.setattr(llm_module, "get_provider", guarded)
    monkeypatch.setattr(llm_module, "_live_provider", _no_live_provider)


@pytest.fixture
def unpatched_get_provider():
    """The real `get_provider`, for test 26."""
    return real_get_provider


# -- 5. Razorpay: never reached over the network in a test -----------------


class RazorpayStub:
    """Stands in for `razorpay_client.create_order`. Counts every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_order(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "id": f"order_TEST{len(self.calls):08d}",
            "amount": kwargs["amount_paise"],
            "currency": kwargs["currency"],
            "receipt": kwargs["receipt"],
            "status": "created",
        }

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def razorpay(monkeypatch) -> RazorpayStub:
    stub = RazorpayStub()
    monkeypatch.setattr(razorpay_client, "create_order", stub.create_order)
    return stub


# -- 6. injected time ------------------------------------------------------


@pytest.fixture
def now() -> datetime:
    """The wall clock is never read by a test. This is `now` for all of them."""
    return datetime.now(timezone.utc).replace(microsecond=0)
