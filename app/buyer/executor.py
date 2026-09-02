"""The tool dispatcher (BUILD_SPEC §11.1).

`agent.py` owns the loop and the state machine. This module owns the context a
tool runs in and the table that routes a call to its implementation; the ten
implementations themselves are in `commerce.py` (the seven that talk to the
merchant) and `purchase.py` (the three that touch authority).

Three rules hold for every implementation.

1. **The LLM never sets state.** Each tool returns the state its success
   implies, and `agent.py` checks that move against the §11.3 table before
   applying it. A tool that fails moves nothing.
2. **Merchant text is wrapped, never edited.** Every string that came from a
   merchant reaches the model inside `<untrusted_merchant_data>` exactly as it
   arrived — PK-005's description included, and especially. Sanitising it here
   would defend the wrong layer and destroy the demonstration (§16.5).
3. **No sum reaches the platform from the model.** `propose_mandate` and
   `submit_purchase` name a quote; every figure is read server-side off the row
   the merchant signed.

The merchant is reached only through `client.py`, over HTTP (invariant 3). The
Guard is reached only through `app.platform.payments`, which owns the single
`guard.evaluate()` call site (§9.4).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.audit import emit
from app.buyer.client import MerchantCallFailed, MerchantClient
from app.errors import KavachError
from app.models import AgentSession, Mandate

# §11.5 — the two capabilities a merchant must advertise to be transactable.
REQUIRED_CAPABILITIES = ("quote.signed", "checkout.submit")


@dataclass
class ToolOutcome:
    """What one tool call produced."""

    result: dict[str, Any]
    # The state the dispatcher should move to, or None to stay put. Applied by
    # `agent.py` against the §11.3 table — never by the model.
    next_state: str | None = None
    # Set by propose_mandate: the loop pauses and waits for the human.
    awaiting_authorization: dict[str, Any] | None = None


@dataclass
class ToolContext:
    """Everything a tool implementation is allowed to reach."""

    db: Session
    session: AgentSession
    client: MerchantClient = field(init=False)

    def __post_init__(self) -> None:
        self.client = MerchantClient(correlation_id=self.session.correlation_id)

    @property
    def correlation_id(self) -> str:
        return self.session.correlation_id

    def emit(self, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        emit(
            self.db,
            correlation_id=self.correlation_id,
            session_id=self.session.id,
            event_type=event_type,
            actor=actor,
            payload=payload,
        )
        self.db.commit()

    def active_mandate(self, mandate_id: str) -> Mandate | None:
        """The named mandate, if it is ACTIVE and belongs to this session.

        Read by `agent.py` to decide whether a second submit attempt may go
        ahead without asking the human the same question twice. It grants
        nothing: the Guard verifies the signature and runs all nine rules on
        every submission regardless of what this returns.
        """
        mandate = self.db.get(Mandate, mandate_id)
        if mandate is None or mandate.status != "ACTIVE":
            return None
        if mandate.session_id and mandate.session_id != self.session.id:
            return None
        return mandate


ToolHandler = Callable[[ToolContext, dict], ToolOutcome]


def _handlers() -> dict[str, ToolHandler]:
    """The §11.2 tool table.

    Imported inside the function because `commerce` and `purchase` import
    `ToolContext` and `ToolOutcome` from this module; one of the two directions
    has to be the late one, the same way `payments.py` defers its import of the
    merchant service.
    """
    from app.buyer import commerce, purchase

    return {
        "discover_merchants": commerce.discover_merchants,
        "get_merchant_profile": commerce.get_merchant_profile,
        "get_catalog": commerce.get_catalog,
        "check_availability": commerce.check_availability,
        "create_cart": commerce.create_cart,
        "add_to_cart": commerce.add_to_cart,
        "request_quote": commerce.request_quote,
        "propose_mandate": purchase.propose_mandate,
        "submit_purchase": purchase.submit_purchase,
        "report_finding": purchase.report_finding,
    }


def execute(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Run one tool call. Never raises for an expected failure.

    A refusal from the merchant or the Guard is information the agent re-plans
    against, so it comes back as a tool result naming the same §18.2 code a
    human would see in the API — not as an exception that kills the turn.
    """
    handler = _handlers().get(name)
    if handler is None:
        return ToolOutcome(
            result={
                "status": "ERROR",
                "code": "UNKNOWN_TOOL",
                "detail": f"There is no tool called {name!r}.",
            }
        )
    try:
        return handler(ctx, arguments or {})
    except MerchantCallFailed as exc:
        return ToolOutcome(result=exc.as_tool_result())
    except KavachError as exc:
        return ToolOutcome(
            result={"status": "ERROR", "code": exc.code, "detail": exc.message}
        )


def to_json(value: Any) -> str:
    """Compact JSON for a tool result going back into context."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
