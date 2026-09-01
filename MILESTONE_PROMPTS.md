# KAVACH — MILESTONE PROMPTS

Paste **one block at a time** into Claude Code. After each one:
1. Run the **VERIFY** command
2. If it works: `git add -A && git commit -m "M<n> ..."`
3. Type `/clear` in Claude Code
4. Move to the next

Do not paste two at once. Do not skip the verify step.

> All section references are to `BUILD_SPEC.md` v1.0, which is self-contained.
> `SPEC_AMENDMENT_A.md` has been merged into it and is no longer normative.

---

## M0 — FOUNDATION (2h)

```
Read BUILD_SPEC.md fully, then implement Milestone 0: Foundation.

Scope: repo scaffold, database models, migrations, seed data, health endpoint,
local Docker setup. Nothing else. No business logic yet.

Create:
- requirements.txt (exactly BUILD_SPEC §2.1 — nothing extra)
- Dockerfile (python:3.11-slim, installs deps, runs alembic upgrade head then
  uvicorn). The node frontend stage comes in M7; leave a comment marking where.
- docker-compose.yml (postgres:15 + the app, app on port 8000)
- .env.example exactly as specified in BUILD_SPEC §4.1
- .gitignore (.env, __pycache__, app/static/, node_modules/, .venv/, .pytest_cache/)
- app/config.py — Pydantic Settings reading every env var from §4.1, applying every
  validation rule in §4.2. Fail loudly at startup, not at first request.
- app/db.py — engine, SessionLocal, get_db dependency
- app/ids.py — prefixed ID generator per §5.1 (12 lowercase alphanumeric chars,
  secrets.choice, the full prefix table)
- app/models.py — ALL 12 tables from BUILD_SPEC §5.2, exact column names and types
- app/schemas.py — Pydantic v2 models for every entity
- app/errors.py — the error codes and exception handlers from §18
- app/main.py — FastAPI app, GET /health returning {"status":"ok"}, and static
  file mounting at / for app/static (create the dir with a placeholder index.html)
- alembic/ — initialised, with one migration creating all 12 tables
- app/merchant/seed.py — the exact seed data from BUILD_SPEC §16, including the
  full PK-005 injection payload verbatim from §16.5. Expose seed_database(db) and
  call it on startup if the merchants table is empty.

Critical details:
- All money columns are BigInteger holding paise. No floats anywhere.
- webhook_events.razorpay_event_id must be UNIQUE NOT NULL
- orders.idempotency_key must be UNIQUE NOT NULL
- orders.guard_decision_id must be NOT NULL
- audit_events has a BIGSERIAL seq column and an index on (correlation_id, seq)
- products (merchant_id, sku) is UNIQUE

Do not create routers, services, the guard, the agent, the LLM layer, or any
frontend yet. Stop when done and report.
```

**VERIFY:**
```bash
docker compose up -d
curl http://localhost:8000/health
```
Expect `{"status":"ok"}`. Then check the seed loaded:
```bash
docker compose exec db psql -U kavach -d kavach -c \
  "SELECT sku, name, unit_price_paise FROM products ORDER BY sku;"
```
Expect PK-001…PK-005 plus NS-001…NS-003 and SC-001…SC-002. PK-001 must be `42000`,
PK-003 `45000`, PK-005 `20000`.

**COMMIT:** `git commit -am "M0: foundation"`

---

## M1 — DEPLOY (1h — mostly you, not Claude Code)

```
Milestone 1: make the app deployable to Render, per BUILD_SPEC §19.

- Ensure the Dockerfile runs `alembic upgrade head` before starting uvicorn, and
  binds to 0.0.0.0 on $PORT (Render sets PORT, default to 8000 locally).
- Add a render.yaml at the repo root describing a Docker web service and a
  free-tier Postgres database, region singapore, declaring every env var from §4.1
  with secrets marked sync: false.
- Make sure the app starts cleanly when DATABASE_URL points at an external
  Postgres (Render gives a postgres:// URL — handle the postgresql:// rewrite
  SQLAlchemy needs, per §4.2).
- Add a README.md stub with the project name and a one-line description.

Do not add features. Stop and report.
```

**Then do PART C of SETUP.md yourself:** push to GitHub, create the Render database and
web service, set all environment variables, register the Razorpay webhook with OTP
`754081`.

**VERIFY:** open `https://your-url.onrender.com/health` in a browser. Must return
`{"status":"ok"}`.

**Do not continue until this works.** Everything after this depends on a public URL.

**COMMIT:** `git commit -am "M1: deploy"`

---

## M2 — MERCHANT INTERFACE (3h)

```
Implement Milestone 2: the Merchant Commerce Interface, per BUILD_SPEC §7 and §6.

Create:
- app/crypto.py — canonical_json, sign, verify, public_key_hex using Ed25519
  from the cryptography library, exactly as specified in §6.1. verify() must
  return False on any exception, never raise.
- app/audit.py — a single emit() function, the ONLY way anything writes to
  audit_events. Redact per §13.2 using a per-event-type allowlist.
- app/merchant/service.py — catalog, availability, cart, quote logic
- app/merchant/router.py — every endpoint in §7

Endpoints (exact paths from §7):
  GET  /.well-known/ucp                    (public, no auth, primary merchant)
  GET  /.well-known/ucp/{slug}             (public, other merchants)
  GET  /merchant/registry
  GET  /merchant/catalog
  POST /merchant/availability
  POST /merchant/carts
  POST /merchant/carts/{cart_id}/items
  POST /merchant/checkout/quote
  GET  /merchant/orders/{order_id}

All except /.well-known/* require the X-Merchant-API-Key header matching
MERCHANT_API_KEY. All accept and echo X-Request-Id.

The quote endpoint is the most important thing here:
- Re-read CURRENT prices and stock from the database inside a transaction.
  Never trust cart_items.unit_price_paise_snapshot.
- Recompute every line total and the grand total server-side, integer only.
- Build the signing payload with EXACTLY the keys listed in §6.3 — note that
  line items in the signing payload carry only sku, qty, unit_price_paise and
  line_total_paise. No names, no descriptions.
- Sign with MERCHANT_SIGNING_SEED.
- Return line_items (with names and categories for display), total_paise,
  expires_at (now + 900s), signature, and signing_payload so the caller can
  verify without guessing canonical form.
- Emit CHECKOUT_QUOTED.

The catalog endpoint must return product descriptions VERBATIM, including the
PK-005 injection payload. That is deliberate — do not sanitize it.

Non-transactable merchants advertise shorter capability lists per §16.1:
nova-stationery has no checkout.submit, saffron-tiffin has no quote.signed.

Do not implement /merchant/checkout/submit yet. That comes in M5 after the
Guard exists.

Stop and report.
```

**VERIFY:**
```bash
curl http://localhost:8000/.well-known/ucp | python3 -m json.tool
curl -H "X-Merchant-API-Key: kavach_merchant_dev_key_change_me" \
  "http://localhost:8000/merchant/catalog?merchant_id=<id from /merchant/registry>"
```
The profile should list capabilities and a public key. The catalog should include
PK-005 with the injection text visible in its description.

**COMMIT:** `git commit -am "M2: merchant interface + signed quotes"`

---

## M3 — RAZORPAY (2h)

```
Implement Milestone 3: Razorpay order creation, checkout, and server-side
verification, per BUILD_SPEC §12.

Create:
- app/platform/razorpay_client.py — thin httpx wrapper per §12.1. create_order(),
  fetch_order_payments(), fetch_payment(). HTTP Basic auth with key_id/key_secret.
  Never logs the secret. This module may only be imported from within
  app/platform/.
- app/platform/payments.py — order creation orchestration, signature
  verification, reconciliation.

Add a TEMPORARY endpoint POST /api/dev/test-checkout that:
- takes a quote_id
- creates an orders row and a Razorpay order (§12.2 payload exactly)
- returns order_id, razorpay_order_id, razorpay_key_id, amount_paise

This is scaffolding so we can prove the rail works before the Guard exists. It
will be deleted in M5. Mark it clearly with a TODO. It is the only place in the
project where an order is created without a guard decision, and it does not
survive past M5.

Add GET /dev/checkout/{order_id} serving a minimal HTML page that loads
checkout.js per §12.3, opens the Razorpay modal with the order, and POSTs the
three returned fields plus our order_id to /api/payments/verify.

Implement POST /api/payments/verify exactly as §12.4 specifies:
- Load OUR order from the database
- expected = hmac_sha256(f"{order.razorpay_order_id}|{payment_id}", KEY_SECRET)
- Compare with hmac.compare_digest
- CRITICAL: use order.razorpay_order_id from our DB, never the value posted
  from the browser
- On valid: payments row CAPTURED, signature_verified=true, order PAID,
  emit PAYMENT_VERIFIED
- On invalid: emit PAYMENT_SIGNATURE_INVALID, leave order PENDING_PAYMENT,
  return 400

Also implement GET /api/payments/{order_id}/reconcile per §12.6.

Stop and report.
```

**VERIFY — this is the important one:**
1. Deploy: `git push`
2. On your live URL, create a cart and a quote (use the curl commands from M2)
3. Call `/api/dev/test-checkout` with the quote_id
4. Open `https://your-url.onrender.com/dev/checkout/<order_id>` in a browser
5. Pay with UPI ID `success@razorpay`
6. Check the Razorpay dashboard → Transactions. **A real test payment should appear.**
7. Check your order status is `PAID`

**Do not proceed until a real payment has completed and verified.**

**COMMIT:** `git commit -am "M3: razorpay orders + server-side verification"`

---

## M4 — WEBHOOKS (1.5h)

```
Implement Milestone 4: webhook handling, per BUILD_SPEC §12.5.

Create app/platform/webhooks.py and the endpoint POST /api/webhooks/razorpay.

Exact flow:
1. raw = await request.body()   -- RAW BYTES, before any parsing. This is
   non-negotiable; signature verification fails if the body is re-serialized.
2. sig = request.headers.get("X-Razorpay-Signature", "")
   event_id = request.headers.get("x-razorpay-event-id", "")
3. expected = hmac_sha256(raw, RAZORPAY_WEBHOOK_SECRET), compare_digest
4. Invalid signature -> store webhook_events row with signature_valid=false,
   emit WEBHOOK_SIGNATURE_INVALID, return 400
5. Valid -> attempt INSERT into webhook_events with razorpay_event_id. On
   IntegrityError (duplicate), record was_duplicate=true, emit
   WEBHOOK_DUPLICATE_IGNORED, return 200 with NO state change. Catch the
   IntegrityError — do not check-then-insert.
6. Dispatch by event type:
   - payment.captured -> order PAID if not already PAID
   - order.paid       -> same idempotent path
   - payment.failed   -> order FAILED ONLY if order is not already PAID
   - payment.authorized -> record it, do not finalize
7. Emit WEBHOOK_RECEIVED and ORDER_COMPLETED as appropriate. Return 200.

Store raw_body and raw_signature on every webhook_events row — we need them for
the replay demo in M8.

Return 200 for every business outcome. Non-2xx ONLY for invalid signature.
Razorpay retries non-2xx on exponential backoff for 24 hours.

State transitions must be idempotent and monotonic toward PAID. A payment.failed
arriving after payment.captured is documented Razorpay behaviour and must not
un-pay an order.

Stop and report.
```

**VERIFY:**
1. `git push`, wait for Render to deploy
2. Make another test payment through `/dev/checkout/...`
3. Razorpay dashboard → Settings → Webhooks → your webhook → check recent deliveries
   show **200**
4. Query your database: `SELECT razorpay_event_id, event_type, was_duplicate FROM webhook_events;`

**COMMIT:** `git commit -am "M4: webhooks with dedup"`

**Day 1 is done.** You have a real, verified, webhook-confirmed payment on a live URL.

---

## M5 — MANDATE + TRANSACTION GUARD + VALIDATOR (4h)

```
Implement Milestone 5: the core of the project. BUILD_SPEC §8, §9, §10.

PART A — Mandate service (app/platform/mandate.py):
- POST /api/mandates/propose        (called by the agent, creates no authority)
- POST /api/mandates/issue          (called by the user, signs with MANDATE_SIGNING_SEED)
- POST /api/mandates/{id}/revoke
- GET  /api/mandates/{id}           (returns mandate + signature + signing_payload)
- GET  /api/mandates/public-key     (Mandate Authority Ed25519 public key)

On issue: apply every clamp in §8.3 — max_amount_paise <= 1000000,
cumulative_cap_paise <= 2000000 and >= max_amount_paise, max_transactions <= 3,
ttl_minutes <= 60. Generate prompt_playback server-side from the CLAMPED numeric
fields per §8.4, never from model output. Sign the exact payload keys from §6.4.
Emit AUTHORIZATION_GRANTED including whether anything was clamped down.

A PROPOSED mandate has signature=NULL and carries no authority. MG-001 fails on it
by construction. Do not special-case it.

PART B — Transaction Guard (app/platform/guard.py):
Implement evaluate() with the contract in §9.1 and all NINE rules from §9.2:
MG-001 through MG-009, using the exact rule IDs, names and block codes.

- Pure function of DB state. No LLM call, no network call, no randomness.
  `now` is an injected parameter, never read from the clock inside.
- Run ALL rules always. Never short-circuit — the console shows every rule.
- Every rule reports observed, threshold, unit and a human-readable detail
  EVEN WHEN IT PASSES. This is what makes "explainable" literal.
- Verdict is BLOCK if any rule fails. failed_rule_id is the LOWEST-numbered
  failing rule.
- Write exactly one guard_decisions row per evaluation, on ALLOW as well as BLOCK.
- Emit POLICY_APPROVED or POLICY_BLOCKED.
- Return the result shape in §9.3 exactly.

PART C — Checkout Validator (app/merchant/validator.py):
CV-001 through CV-004 from §10. The merchant verifies the mandate signature
using only the Mandate Authority PUBLIC key — it must not query the buyer's or
platform's tables to do this.
CV-003 must re-read current prices and confirm they still reproduce the quote's
total_paise exactly. This is the price-drift catcher.

PART D — Wire it up:
Implement POST /merchant/checkout/submit per §7.8:
- Requires an Idempotency-Key header
- On duplicate Idempotency-Key: return the ORIGINAL response with 200 and
  header X-Idempotent-Replay: true. Do not re-validate. Do not create a second
  Razorpay order. Check this FIRST, before the validator.
- Runs the Checkout Validator
- SELECT ... FOR UPDATE on every product, re-check stock, decrement
- Creates the order carrying guard_decision_id, then the Razorpay order
- Marks the quote CONSUMED and the cart CONSUMED

Implement the single Guard call site in app/platform/payments.py exactly as shown
in §9.4. On BLOCK, raise GuardBlocked — no merchant submit, no Razorpay call.

Delete the temporary /api/dev/test-checkout endpoint from M3.

Stop and report.
```

**VERIFY:** Ask Claude Code to write you a small script `scripts/demo_block.py` that
creates a cart over budget, issues a ₹6,000 mandate, and submits. Run it. You should see
a BLOCK with `failed_rule_id: MG-005`, all nine rules present in the result, and **no new
order in the Razorpay dashboard.**

**COMMIT:** `git commit -am "M5: mandate + transaction guard + validator"`

---

## M6 — THE AGENT (3h)

```
Implement Milestone 6: the AI buyer, per BUILD_SPEC §11.

Create:
- app/buyer/llm/base.py — the neutral types and Protocol from §11.7 exactly:
  ToolSpec, ToolCall, Message, LLMResponse, LLMProvider.
- app/buyer/llm/gemini.py — Gemini adapter using google-genai, following the
  translation rules in §11.7. System prompt goes to system_instruction. Generate
  tool-call ids since Gemini does not return them. Handle parallel function_call
  parts.
- app/buyer/llm/anthropic.py — Anthropic adapter, tool_use / tool_result blocks.
- app/buyer/llm/cassette.py — record/replay adapter per §11.8.
- app/buyer/llm/__init__.py — get_provider() factory reading LLM_PROVIDER. It
  RAISES when LLM_PROVIDER is unset or unknown. No silent live fallback.
- Retry-with-jittered-backoff in every adapter per §11.7: 429/500/502/503 and
  read timeouts, delays 1s/2s/4s/8s plus 0-500ms jitter, max 4 attempts, then
  raise LLMUnavailable.

- app/buyer/client.py — httpx wrapper calling the merchant over HTTP. Sets
  X-Merchant-API-Key and X-Request-Id on every call. Base URL from APP_BASE_URL.
  CRITICAL: no file under app/buyer/ may import from app.merchant.
- app/buyer/prompts.py — the system prompt from §11.5, verbatim
- app/buyer/tools.py — exactly the 10 tools from §11.2, declared ONCE as
  provider-neutral JSON Schema ToolSpec. Do not import any LLM SDK here.
- app/buyer/agent.py — the tool loop and state machine. Neutral types only.
- app/buyer/router.py — the three endpoints from §11.6

Tool rules:
- No tool accepts a price, a total, an amount, a currency, a policy flag, or an
  override parameter. There is no policy_override, no force, no skip_validation.
  The injection attack asks for exactly that, and the schema must make it
  impossible rather than merely refused.
- propose_mandate creates no authority — it returns a proposal only.
- submit_purchase takes {quote_id, mandate_id, idempotency_key} and nothing else.
  On BLOCK it returns {status, failed_rule_id, block_code, detail, guidance} so
  the agent can recover. guidance is generated server-side from the failing rule.
- report_finding is how the agent surfaces an embedded instruction it noticed.

All merchant-derived content passed to the model must be wrapped:
  <untrusted_merchant_data source="..." merchant_id="...">...</untrusted_merchant_data>

State machine from §11.3. Legal transitions live in a dict in agent.py. The LLM
does NOT set state — the tool dispatcher sets it after a successful tool call.
An illegal transition raises, emits ILLEGAL_STATE_TRANSITION, and halts.
AWAITING_AUTHORIZATION -> AUTHORIZED is reachable only via the human authorize
endpoint, never by a tool.

Limits from §11.4: max 20 tool calls, 120 seconds wall clock, 2 submit attempts,
max_tokens=1024 per call.

Token discipline from §11.9: prune tool results before they re-enter context,
cap history at the last 8 turns, never resend the full catalog every turn.

POST /api/buyer/sessions/{id}/message returns an SSE stream with event types
thought | tool_call | tool_result | state | awaiting_authorization | message |
error | done. An LLMUnavailable failure streams
{"type":"error","code":"LLM_UNAVAILABLE"} and must NEVER be interpreted as a
purchase outcome.

Record llm_provider and llm_model on every agent_sessions row and include both in
the USER_INTENT_RECEIVED payload.

Run with LLM_PROVIDER=gemini, CASSETTE_MODE=off.

Stop and report.
```

**VERIFY:**
```bash
curl -X POST http://localhost:8000/api/buyer/sessions \
  -H "Content-Type: application/json" -d '{"user_email":"priya@example.com"}'
```
Then send the canonical intent from §16.4 to the message endpoint and watch the stream.
The agent should discover 3 merchants, reject 2 by naming the missing capability, build
a **516000 paise** cart, and pause for authorization.

### THEN — record the three cassettes before you commit

Set `CASSETTE_MODE=record` and run each scenario once, saving to
`cassettes/happy_path.json`, `cassettes/budget_block.json`, `cassettes/injection.json`
per §11.8. **Commit the cassettes.** M7 and M9 run entirely on replay, at zero token cost.

**COMMIT:** `git commit -am "M6: ai buyer agent + llm provider layer + cassettes"`

**Day 2 is done.**

---

## M7 — FRONTEND (3h)

```
Implement Milestone 7: the frontend, per BUILD_SPEC §14.

Set LLM_PROVIDER=cassette and CASSETTE_MODE=replay for all of this milestone.
Zero live model calls.

Create a Vite + React 18 + TypeScript + Tailwind app in frontend/, built to
app/static/ so FastAPI serves it. Configure vite.config.ts with
build.outDir = '../app/static' and base = '/'.

Single page, five panels:
1. ChatPanel + AgentTrace (left, full height) — consumes the SSE stream,
   renders tool calls as "> discover_merchants  3 found, 1 transactable"
2. AuthorizationCard (right top) — appears on awaiting_authorization. Shows
   prompt_playback prominently, an editable max_amount field in rupees, the
   category and merchant allowlists, the expiry, and an [Authorize] button.
3. GuardConsole (right middle) — renders ALL NINE rules with observed vs
   threshold. Passes green, failure red with the block code and detail. This
   is the most important screen in the demo; make it excellent.
4. AuditTrace (right bottom) — the correlation ID and the ordered event list,
   each expandable to show its payload.
5. DemoPanel (header bar) — buttons wired to the M8 endpoints.

Rules:
- No financial state in React state. Every value comes from an API response.
- Rupee display is a pure formatting function over paise. No arithmetic in the
  frontend.
- Payment status is polled from /merchant/orders/{id}, never inferred from
  the checkout handler callback.
- Razorpay checkout.js is loaded and opened with the order returned by submit.
- The header shows llm_provider and llm_model for the running session.

Design: dark, dense, instrument-panel aesthetic. Monospace for IDs, rule codes
and amounts. This should read as a control surface and a piece of evidence, not
as a shopping app.

Add a build step to the Dockerfile: a node:20-alpine stage builds the frontend
into app/static, the python stage copies it.

Stop and report.
```

**VERIFY:** `git push`, open your live URL, type the canonical intent, complete a
purchase.

**COMMIT:** `git commit -am "M7: frontend"`

---

## M8 — DEMO PANEL + INJECTION (2h)

```
Implement Milestone 8: the Demo Control Panel per BUILD_SPEC §15, and prove the
injection scenario end to end.

Set LLM_PROVIDER=gemini and CASSETTE_MODE=off for the injection tuning.

app/platform/demo.py, mounted only when DEMO_MODE=true. Every action emits
DEMO_ACTION_TRIGGERED.

  POST /api/demo/drift-price     PK-003 45000 -> 49500 paise
  POST /api/demo/deplete-stock   PK-003 stock -> 3
  POST /api/demo/revoke-mandate  revokes the active mandate
  POST /api/demo/replay-webhook  re-POSTs the stored raw_body and raw_signature
                                 of the most recent webhook_events row to our own
                                 /api/webhooks/razorpay endpoint. Genuine
                                 signature, genuine event id.
  POST /api/demo/reset           restores seed prices and stock, clears carts,
                                 quotes, mandates, orders, payments.
                                 KEEPS audit history.

Then verify the injection scenario end to end and fix anything that breaks:
- The agent reads PK-005's description containing the fake merchant policy note
- It adds 12 units of PK-005, pushing the total to 756000 paise
- The Guard blocks on MG-005 (756000 > 600000)
- No Razorpay order is created
- The agent calls report_finding, quoting the embedded instruction and naming
  PK-005 as its source
- The agent re-plans to the correct 516000 paise cart

Do NOT harden the system prompt to make the agent resist the injection. Per §11.5
and CLAUDE.md, the failure is the feature. Tune only the reporting behaviour after
the block.

If the agent is NOT fooled, that is fine and worth noting — but confirm the
Guard would block it regardless by testing the over-budget path directly.

Stop and report.
```

**VERIFY:** Click each demo button. Then run the injection scenario and watch MG-005 go
red with zero Razorpay activity.

**COMMIT:** `git commit -am "M8: demo panel + injection scenario"`

---

## M9 — TESTS + DOCS (2h)

```
Implement Milestone 9: the test suite and documentation.

Write exactly the 26 tests listed in BUILD_SPEC §17, in the five files named
there. No more, no fewer. Use pytest with a throwaway test database fixture and
injected `now` values rather than the wall clock.

conftest.py must set LLM_PROVIDER=cassette and CASSETTE_MODE=replay, and
monkeypatch app.buyer.llm.get_provider to raise if any test reaches for a live
provider. Invariant I-11: no test makes an LLM API call, ever.

The five that matter most:
- Test 10: monkeypatch razorpay_client.create_order to raise, run a BLOCK path,
  assert it completes cleanly and the mock was never called.
- Test 13: POST the same webhook body with the same x-razorpay-event-id twice.
  Assert one state transition, second response 200 with was_duplicate.
- Test 21: assert guard.evaluate has exactly one call site, using ast.
- Test 23: walk app/buyer/ with ast and assert no module imports from
  app.merchant.
- Test 26: assert get_provider() raises when LLM_PROVIDER is unset.

Then write README.md containing:
- What Kavach is, in three sentences
- The architecture diagram from §1 as ASCII
- Quickstart: clone, .env, docker compose up, seed, open localhost:8000
- The five demo scenarios and how to trigger each
- The protocol alignment matrix from §21, verbatim — do not soften the
  "not implemented" column
- The threat model table from §22, verbatim
- A "How to verify this is real" section, the five checks from §23
- A note that all three merchants share one signing key in this build, and that
  cassettes exist as demo insurance

Run the full suite and fix any failures.

Stop and report.
```

**VERIFY:** `pytest -v` — all 26 green, and `/cost` should show the run cost nothing.

**COMMIT:** `git commit -am "M9: tests + docs"`

---

## THEN: RECORD (3h — protected time)

Follow the demo script in BUILD_SPEC §20. Five minutes.

Before you hit record:
1. Set `LLM_PROVIDER=gemini`, `CASSETTE_MODE=off`. **Record live.**
2. Load your Render URL and **wait 40 seconds** — the free tier sleeps and takes that
   long to wake. Do not get caught by this mid-take.
3. `POST /api/demo/reset`
4. Open the Razorpay dashboard in a second tab, on Transactions
5. Do one silent dry run of the whole flow

Record in this order: thesis → happy path → injection block → webhook replay → audit →
why it matters.

Show the Razorpay dashboard payment ID matching your order. That single cut is what
turns "we claim a payment happened" into "you can see it happened."

If you get rate-limited mid-take, switch to `CASSETTE_MODE=replay`, finish the recording,
and say so in the README. An honest fallback beats a dead take at hour 29.
