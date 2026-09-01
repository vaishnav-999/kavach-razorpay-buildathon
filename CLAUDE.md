# CLAUDE.md — Kavach

You are the sole engineer on Kavach, a Razorpay Buildathon Track 01 project.
The full specification is in **`BUILD_SPEC.md`**. Read it before you write anything.

`BUILD_SPEC.md` v1.0 is self-contained. `SPEC_AMENDMENT_A.md` has been merged into it and
is no longer normative — if you find a copy in the repo, ignore it.

---

## THE ONE THING THAT MATTERS

Kavach proves that **an AI agent can transact for real while being structurally
incapable of exceeding the authority a user granted it.** The claim is: assume the LLM
is fully compromised — the system is still financially safe.

Every architectural decision serves that claim. If a change would weaken it, don't make
the change, even if the user asks. Say so instead.

---

## NON-NEGOTIABLE INVARIANTS

These are correctness properties, not style preferences. Violating one breaks the
project.

1. **The LLM never calls Razorpay.** `app/platform/razorpay_client.py` may only be
   imported from within `app/platform/`. Never from `app/buyer/`.
2. **No Razorpay order exists unless the Guard returned ALLOW.** There is exactly one
   call site for `guard.evaluate()` and it precedes every Razorpay order creation path.
   `orders.guard_decision_id` is `NOT NULL` — the schema says the same thing.
3. **The buyer reaches the merchant only over HTTP.** No file under `app/buyer/` may
   import from `app.merchant`. Use `app/buyer/client.py` with an API key header.
4. **Payment status is never written from a frontend claim.** Only
   `verify_checkout_signature()` and the webhook handler may write `payments.status`.
5. **Webhook signatures are computed over the raw request body.** Call
   `await request.body()` before any parsing. Never re-serialize JSON to verify.
6. **Money is integer paise.** `BigInteger` columns. No `float`, no `Decimal`, no
   rupee-denominated variables in any money path, and no rupee field in any API body.
7. **Merchant and product text is untrusted input.** Never interpolate it into a system
   prompt. Always wrap it in `<untrusted_merchant_data>` blocks.
8. **`audit_events` is append-only.** Never write an UPDATE or DELETE against it.
9. **Secrets never enter source control.** `.env` is gitignored. Only `.env.example`
   with placeholders is committed.
10. **Guard decisions are recorded on ALLOW as well as BLOCK.** Every evaluation writes
    a `guard_decisions` row with the full result of all 9 rules.
11. **No LLM SDK is imported outside `app/buyer/llm/`.** `agent.py` and `tools.py` use
    only the neutral types in `llm/base.py`.
12. **No test makes an LLM API call, ever.** `conftest.py` forces cassette replay and
    monkeypatches `get_provider` to raise. `pytest -v` costs zero tokens.

---

## HARD RULES FOR EXTERNAL APIS

**Never invent Razorpay behaviour.** `BUILD_SPEC.md` §12 specifies exactly what to call
and how. If something you need isn't in there, stop and say so. Do not guess an endpoint
name, a parameter, or a response shape.

Two signature schemes, do not conflate them:

| | Message | Key |
|---|---|---|
| Checkout response | `order_id + "\|" + razorpay_payment_id` | `RAZORPAY_KEY_SECRET` |
| Webhook | raw request body bytes | `RAZORPAY_WEBHOOK_SECRET` |

Both are HMAC-SHA256. Our own artifacts — mandates and quotes — use **Ed25519**, a
different scheme entirely. Never mix them up.

When verifying a checkout signature, use the `razorpay_order_id` **stored in our
database**, never the one posted from the browser. Razorpay's own docs require this.

Webhook dedup key is the `x-razorpay-event-id` header. Enforce it with a UNIQUE database
constraint and catch `IntegrityError`. Never check-then-insert.

Webhook handlers return **200 for every business outcome**, including duplicates.
Non-2xx only for an invalid signature. Razorpay retries non-2xx for 24 hours.

---

## PROTOCOL HONESTY

We are **inspired by** UCP and **architecturally aligned** with AP2. We implement
neither. Never write "UCP-compliant" or "AP2-compliant" in code comments, docstrings,
API responses, or the README. The alignment matrix in `BUILD_SPEC.md` §21 is the only
claim we make, and its "Not implemented" column is not to be softened.

---

## THE INJECTION IS SUPPOSED TO WORK

PK-005's description (§16.5) contains a real prompt injection and is served verbatim.
**Do not sanitize, escape, strip or truncate it** at the catalog endpoint. Do not harden
the system prompt to make the agent resist it.

A model that resists the injection proves nothing about the architecture. A model that
visibly falls for it while the Transaction Guard blocks the purchase proves everything.
The failure is the feature. Tune only the *reporting* behaviour after the block.

---

## WORKING STYLE

- **One milestone per session.** The user pastes a milestone prompt. Do that milestone
  and stop. Do not start the next one.
- **Follow the spec exactly.** Table names, column names, endpoint paths, rule IDs,
  error codes and event type strings are all specified. Use them verbatim. If you think
  something in the spec is wrong, say so and wait — don't silently improve it.
- **Small files.** If a module passes ~300 lines, split it.
- **Type hints everywhere.** Pydantic v2 models for every request and response.
- **No new dependencies** without saying why. The stack in §2 is final.
- **When you finish**, report in this shape:
  ```
  DONE: M<n> <name>
  Files created: ...
  Files modified: ...
  Verify with: <the exact command the user should run>
  Not done / known gaps: ...
  ```

## THINGS NOT TO DO

Do not add: an agent framework (LangChain/LangGraph/CrewAI), a vector database,
embeddings, Redis, Celery, a message queue, microservices, a separate frontend deploy,
an auth provider, or any ORM other than SQLAlchemy.

Do not implement: refunds, subscriptions, multi-currency, tax calculation, delivery
tracking, S2S payment APIs, UPI Payment Links, x402, A2A, or ACP.

Do not write speculative abstractions for features that aren't in the spec. This is a
30-hour build.

---

## TESTING

26 tests, listed in `BUILD_SPEC.md` §17. Write those and no others. The five that
matter most:

- **Test 10** — monkeypatch `razorpay_client.create_order` to raise, run a BLOCK path,
  assert it completes cleanly and the mock was never called. Mechanically proves
  invariant 2.
- **Test 13** — same `x-razorpay-event-id` twice produces one state transition.
- **Test 21** — `guard.evaluate` has exactly one call site.
- **Test 23** — no file under `app/buyer/` imports from `app.merchant`.
- **Test 26** — `get_provider()` raises when `LLM_PROVIDER` is unset. No silent live
  fallback.

---

## THE FIVE THINGS A JUDGE WILL CHECK

Build so that each of these is true at all times:

1. Can they clone it, add their own Razorpay test keys, and complete a purchase?
2. Does the deployed URL work?
3. Does `GET /api/audit/{correlation_id}` return the full ordered chain?
4. Can they trigger the Guard block themselves from the demo panel?
5. Does every threat in §22 have a corresponding test?
