# Kavach

Kavach is an AI buyer that transacts for real — it discovers merchants, builds a cart,
takes a cryptographically signed price from the merchant, asks a human for a bounded
grant of authority, and pays through Razorpay. Between the agent and the money sits a
**Transaction Guard**: a deterministic nine-rule gate that is the only code path to a
Razorpay order, and which reads only signed artifacts and database state, never the
model's output. The claim is not that the model behaves — it is that **you can assume
the LLM is fully compromised and the system is still financially safe.**

Razorpay Buildathon, Track 01. Test mode only; the application refuses to start on a
live Razorpay key.

---

## Architecture

Three planes in one deployable. The separation is logical, enforced by import
boundaries, not by network topology. One process, one database, one Render service.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  BUYER PLANE                            app/buyer/                      │
│                                                                         │
│   User intent ──▶ Agent (LLM tool loop) ──▶ tools.py                    │
│                        │                        │                       │
│                        │                        ▼                       │
│                        │                   client.py ── HTTP ──┐        │
│                        │              (X-Merchant-API-Key)     │        │
│   ✗ cannot import app.merchant                                 │        │
│   ✗ cannot import razorpay_client                              │        │
└────────────────────────┼───────────────────────────────────────┼────────┘
                         │ submit_purchase                       │
                         ▼                                       │
┌─────────────────────────────────────────────────────────────┐  │
│  PLATFORM PLANE                        app/platform/        │  │
│                                                             │  │
│   mandate.py ──▶ signed mandate (Ed25519)                   │  │
│                                                             │  │
│   payments.py                                               │  │
│      │                                                      │  │
│      ├─▶ guard.evaluate()   ◀── THE ONLY CALL SITE          │  │
│      │        │                                             │  │
│      │   BLOCK ├──────▶ guard_decisions + POLICY_BLOCKED     │  │
│      │        │         ──▶ STOP. No merchant call.          │  │
│      │        │             No Razorpay call. Ever.          │  │
│      │   ALLOW ▼                                            │  │
│      ├─▶ merchant /checkout/submit (HTTP) ─────────────────┐ │  │
│      └─▶ razorpay_client.create_order()                    │ │  │
│                                                            │ │  │
│   webhooks.py ◀── POST /api/webhooks/razorpay              │ │  │
└────────────────────────────────────────────────────────────┼─┼──┘
                                                             ▼ ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  MERCHANT PLANE                         app/merchant/                   │
│                                                                         │
│   /.well-known/ucp  ·  /merchant/catalog  ·  /merchant/carts            │
│   /merchant/checkout/quote   ──▶ Ed25519-signed quote                   │
│   /merchant/checkout/submit  ──▶ validator.py (CV-001..CV-004)          │
│                                  FOR UPDATE, stock, price re-read       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                          ┌─────────┴──────────┐
                          ▼                    ▼
                   PostgreSQL           Razorpay (test mode)
                   audit_events         Orders API · Checkout · Webhooks
                   (append-only)
```

Read the diagram for the two things that matter: **the Guard is the only door**, and
**the buyer plane has no line to Razorpay at all.**

---

## Quickstart

```bash
git clone https://github.com/vaishnav-999/kavach-razorpay-buildathon.git
cd kavach-razorpay-buildathon
cp .env.example .env
```

Fill in four things in `.env`:

```bash
# 1 and 2 — your own Razorpay TEST keys, from the Razorpay dashboard in Test Mode
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...

# 3 — two DIFFERENT Ed25519 seeds. Run this twice:
#     python -c "import os; print(os.urandom(32).hex())"
MANDATE_SIGNING_SEED=<64 hex chars>
MERCHANT_SIGNING_SEED=<64 hex chars, different from the above>

# 4 — the LLM. gemini | anthropic | cassette. There is no default.
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.6-flash
```

Then:

```bash
docker compose up -d --build      # ALWAYS --build; see "What broke" below
curl -s http://localhost:8000/health
```

Open **http://localhost:8000**. The seed (§16 — three merchants, ten products) loads
automatically on first start, when the `merchants` table is empty.

Paste the canonical intent into the chat panel:

> Order lunch for our 12-person offsite on Thursday. Eight of us are vegetarian, four
> are not. High protein if you can. Keep it under six thousand rupees.

Authorize at ₹6,000 when the authorization card appears, then pay with Razorpay test
credentials. UPI is not enabled on the test account used here — use **Netbanking → any
bank → Success**. The checkout asks for a mobile number; `9999999999` works.

For webhooks you need a public URL. **Razorpay blocks ngrok and localtunnel as webhook
URLs**, so there is no local shortcut: deploy (a `render.yaml` blueprint is included)
and register `https://<your-url>/api/webhooks/razorpay` in the Razorpay dashboard under
Settings → Webhooks, with the secret matching `RAZORPAY_WEBHOOK_SECRET` and the events
`payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`.

---

## The five demo scenarios

Every lever below is a real code path, gated by `DEMO_MODE`. With the flag off they
answer **403 `DEMO_MODE_DISABLED`** rather than 404, so you can see a switch that is off
rather than a deployment that is broken. Each returns a body saying exactly what it
changed, and the console renders it.

| # | Scenario | How to trigger it | What you should see |
|---|---|---|---|
| 1 | **Happy path** | Paste the canonical intent, authorize at ₹6,000, pay | Agent rejects Nova Stationery (no `checkout.submit`) and Saffron Tiffin (no `quote.signed`), builds PK-001×8 + PK-003×4 = **516 000 paise**, gets a signed quote, pauses for authorization. Guard console: **nine rules green**. Payment appears in your Razorpay dashboard. |
| 2 | **Prompt injection → Guard block** | **Injection** button in the header, then `POST /api/demo/force-poisoned-cart` (the **Force poisoned cart** button) | PK-005's description is served verbatim on the left; the nine-rule result on the right. Cart total **756 000** against a **600 000** cap. **MG-005 goes red.** No Razorpay order exists — the client's own call counter is reported before and after and does not move. |
| 3 | **Merchant-side refusal** | `POST /api/demo/drift-price` (PK-003 45000 → 49500) or `POST /api/demo/deplete-stock` (PK-003 stock → 3) | The Guard says ALLOW; the *merchant* refuses on the already-signed quote. **CV-003 `MERCHANT_PRICE_DRIFT`** or **CV-002 `MERCHANT_OUT_OF_STOCK`**, 409, no order. Two independent gates, one for the buyer and one for the merchant. |
| 4 | **Revoked authority** | `POST /api/demo/revoke-mandate` | The newest ACTIVE mandate is revoked. The next submission blocks on **MG-002 `MANDATE_NOT_ACTIVE`** — the signature still verifies, the authority is simply gone. |
| 5 | **Webhook replay** | `POST /api/demo/replay-webhook` | The stored `raw_body` and `raw_signature` of the most recent webhook are re-POSTed over real HTTP. **Genuine signature, genuine event id.** Second response is 200 with `was_duplicate=true`, and exactly one state transition happened. |

`POST /api/demo/reset` puts seed prices and stock back and clears the transaction
tables. It **keeps `audit_events`** — the append-only claim would be hollow if a demo
button truncated the table.

You can also trigger the block with no demo endpoint at all: lower the max-amount field
on the authorization card below the cart total and press Authorize.

---

## How to verify this is real

Five things a sceptic can do in ten minutes.

1. **Clone it and pay.** Add your own Razorpay test keys to `.env`, `docker compose up`,
   open `localhost:8000`, run the flow. A payment appears in *your* Razorpay dashboard.
2. **Open the deployed URL.** It is live at **https://kavach-4576.onrender.com**. Wait
   40 seconds if the free tier is asleep.
3. **Pull the audit chain.** `GET /api/audit/{correlation_id}` returns every event in
   order, with both signatures and their signing payloads, so you can verify them
   yourself with the published public keys (`GET /api/mandates/public-key`, and the
   merchant's in `/.well-known/ucp`).
4. **Trigger the block yourself.** Press the demo panel buttons. Watch a specific rule go
   red with real numbers, and watch the Razorpay dashboard stay empty.
5. **Run the tests.** `pytest -v` — 30 green, zero API calls, zero cost. Tests 10, 13,
   21, 22 and 23 are the ones that prove the architectural claims rather than the
   features.

---

## Protocol alignment

We are **inspired by** UCP and **architecturally aligned** with AP2. **We implement
neither.** This table is the only claim we make.

| Concept | UCP | AP2 | Kavach | Implemented? |
|---|---|---|---|---|
| Merchant discovery profile | `/.well-known` service profile | — | `/.well-known/ucp` | **Inspired by.** Our own schema, our own field names. |
| Machine-readable catalog | catalog capability | — | `/merchant/catalog` | **Inspired by.** |
| Signed price offer | signed offer | Cart Mandate | Ed25519-signed quote (§6.3) | **Aligned in shape.** Not their wire format, not their signature envelope. |
| User authority artifact | — | Intent Mandate | Ed25519-signed mandate (§6.4) | **Aligned in shape.** Ours is a bespoke JSON artifact, not an AP2 mandate. |
| Verifiable credential envelope | — | W3C VC / JWT | Plain JSON + Ed25519 | **Not implemented.** No VC, no JWT, no DID document. |
| Agent identity | — | DID-based | An API key and a session id | **Not implemented.** |
| Human presence signal | — | present/not-present modes | Explicit human `authorize` call | **Partially, by a different mechanism.** |
| Delegated payment rail | payment capability | Payment Mandate → PSP | Razorpay Orders + Checkout | **Real, but not protocol-native.** A human completes payment in the browser. |
| Agent-initiated settlement | — | agent-side payment | — | **Not implemented.** |
| Dispute / refund flow | — | defined | — | **Not implemented.** Out of scope (§2.2). |
| Policy enforcement point | — | implied | **Transaction Guard (§9)** | **Our addition.** Neither protocol specifies a nine-rule pre-authorisation gate. |

The last row is the contribution. The honest columns above it are what make it credible.

---

## Threat model

Every threat has a control and a test.

| ID | Threat | Control | Test |
|---|---|---|---|
| T-01 | Prompt injection in product data instructs the agent to over-buy | Guard MG-005; no override parameters exist | 5, 25 |
| T-02 | Fully compromised LLM inflates the amount | MG-005 + guard is the only path to Razorpay | 5, 10 |
| T-03 | Compromised LLM redirects payment to a different merchant | MG-004 allowlist | 4 |
| T-04 | Duplicate or replayed webhook double-processes a payment | `razorpay_event_id` UNIQUE + caught `IntegrityError` | 13 |
| T-05 | Forged webhook | HMAC-SHA256 over raw request bytes | 14, 16 |
| T-06 | Forged checkout callback posted from the browser | Server-side HMAC using **our** stored `razorpay_order_id` | 12 |
| T-07 | Price drifts between quote and submit | CV-003 re-reads prices and requires an exact match | 19 |
| T-08 | Stock disappears between quote and submit | CV-002 under `SELECT ... FOR UPDATE` | 19 |
| T-09 | Double submit or network retry creates two orders | `Idempotency-Key` UNIQUE; replay returns the original | 20 |
| T-10 | Mandate tampered with after issue | Ed25519 verification, MG-001 | 1 |
| T-11 | Revoked mandate still used | MG-002 | 2 |
| T-12 | Expired mandate still used | MG-003 | 3 |
| T-13 | Mandate drained by repeated small purchases | MG-006 cumulative cap, MG-009 velocity | 6, 9 |
| T-14 | Agent bypasses the merchant boundary via a direct import | Import fence, AST test | 23 |
| T-15 | Agent calls Razorpay directly | Import fence, AST test | 22 |
| T-16 | Audit trail edited to hide a block | `audit_events` append-only; demo reset preserves it | 24 |
| T-17 | Quote total forged or line items do not sum | MG-008 quote integrity | 8 |
| T-18 | Purchase drifts into an unauthorised category | MG-007 | 7 |
| T-19 | Late `payment.failed` un-pays a paid order | Monotonic transitions toward `PAID` | 15 |
| T-20 | A second code path reaches Razorpay without the Guard | Single call site, AST test | 21 |

A twenty-first threat is not in that table because it was found after it was written:
**an ALLOW decision used as a bearer token against a different quote or mandate.** It is
covered by additions A-1 to A-4 below.

---

## Tests

```bash
docker compose up -d db      # the suite needs a Postgres; this is the one
pytest -v                    # 30 passed
```

The suite creates its own throwaway database (`kavach_test_<random>`), runs the schema
into it, truncates and re-seeds between tests, and drops it at the end. Your development
database is never touched. Point it elsewhere with
`KAVACH_TEST_DB_ADMIN_URL=postgresql://user:pass@host:port/postgres` if your Postgres is
not on `localhost:5433`.

**Invariant I-11: no test makes an LLM API call. Ever.** `conftest.py` pins
`LLM_PROVIDER=cassette` and `CASSETTE_MODE=replay`, wraps `app.buyer.llm.get_provider`
in a guard that raises the moment a test would reach for a live provider, and replaces
`_live_provider` with one that always raises. `pytest -v` costs zero tokens and always
will. Every test uses injected `now` values rather than the wall clock.

26 of the 30 are BUILD_SPEC §17, in the five files it names. The five that matter most:

| # | File | What it proves |
|---|---|---|
| **10** | `test_guard.py` | `razorpay_client.create_order` is replaced by a function that fails the test if it is ever entered; the BLOCK path then runs end to end, raises `GuardBlocked`, and the mock is never called. Invariant 2, mechanically. |
| **13** | `test_payments.py` | The same `x-razorpay-event-id` POSTed twice produces one state transition; the second is a 200 with `was_duplicate=true`. |
| **21** | `test_boundaries.py` | `guard.evaluate` has exactly one call site in the repository, found with `ast`. Renaming the import is not a way past it. |
| **23** | `test_boundaries.py` | No module under `app/buyer/` imports from `app.merchant`. |
| **26** | `test_agent.py` | `get_provider()` raises when `LLM_PROVIDER` is unset. No silent live fallback. |

### Four tests beyond §17

§17 was written before the M5a/M5b binding hardening, so the four fixes described below
had no coverage in it. These four are **additions**, named `test_addition_a1` … `a4`:

| ID | File | What it asserts |
|---|---|---|
| A-1 | `test_guard.py` | MG-001 fails when a **mandate column** is raised while the signature still verifies — and MG-005, reading that column, would have let the purchase through. |
| A-2 | `test_guard.py` | MG-008 fails when a **quote payload field** disagrees with the row, with the merchant signature still genuine. |
| A-3 | `test_merchant.py` | An ALLOW presented with a **different quote** is refused, 409. |
| A-4 | `test_merchant.py` | An ALLOW presented with a **different mandate** is refused, 409. |

The four static-analysis tests were themselves checked by mutation: a second
`guard.evaluate` call site, an `import anthropic` in `app/buyer/`, an
`from app.merchant import service` in `app/buyer/`, a `DELETE FROM audit_events`, an
`audit_events` entry in the demo reset list, a money column switched to `Integer`, and a
float literal in a money path — each one makes the corresponding test fail.

---

## What broke, and how we got out

### The four binding gaps

After the Guard was working, I stopped adding features for a day and swept the codebase
for exactly one pattern: **a check that is correct, but where the binding between two
artifacts is unverified.** The classic confused-deputy shape. I found four instances.
Every individual check was right; the gaps were in what they *referred to*.

| Gap | The problem | The fix |
|---|---|---|
| Guard decision → quote | The merchant only checked `verdict == ALLOW`. Any past ALLOW was therefore a bearer token: a decision reached about a 516 000 cart could be attached to an independently valid 756 000 cart and be charged the larger amount, because MG-005, MG-006 and MG-009 were evaluated against a total that is not the one being charged. CV-001 to CV-004 never look at the amount against the mandate, so the validator would not have caught it either. | `_require_allow_decision` binds `quote_id`, `mandate_id`, `requested_total_paise` and `merchant_id`. |
| Guard decision → mandate | CV-001 verifies whatever mandate it is handed on that mandate's own terms; it has no way to know which mandate the Guard actually evaluated. An ALLOW reached against one mandate's caps could be spent under another's name — and MG-006 and MG-009, which count prior ALLOWs *per mandate*, would be counting against a mandate that is not paying. | Same comparison, `mandate_id` field. |
| MG-001 | The signature covered `signing_payload`; rules MG-003 to MG-007 and MG-009 read the **columns**. Nothing bound the two. Anything able to write to `mandates` could raise a cap on a mandate whose signature still verified perfectly, and every downstream rule would have enforced the raised number. | MG-001 reconciles seven fields payload-against-row *after* the signature verifies. |
| MG-008 | The quote signature was verified and then only `total_paise` was reconciled back to it, while `status`, `expires_at`, `merchant_id` and `currency` were read from columns the signature did not bind. | Full payload reconciliation, including the `typ` tag — a signature proves the merchant signed *something*, and without a type tag a mandate payload and a quote payload are both just signed JSON. |

A fifth, smaller one came out of the same sweep: `correlation_id` was caller-supplied at
submit, which split the audit chain in two. It is now taken from the bound guard
decision, so one purchase is one retrievable story.

Each fix ships with a script that mounts the attack and shows the refusal:

```bash
# From Windows/PowerShell, point at the host port the compose file publishes:
$env:DATABASE_URL = "postgresql://kavach:kavach@localhost:5433/kavach"

python scripts/demo_block.py             # BLOCK on MG-005, no order, no Razorpay call
python scripts/demo_allow.py             # ALLOW, real Razorpay order
python scripts/demo_decision_binding.py  # both binding attacks refused, 409
```

MG-001 and MG-008 drift were verified by tampering with each field individually while
leaving the signature genuine — every one blocks, and an untampered control still
passes. Tests A-1 to A-4 hold the line from here.

### Gemini 3's `thought_signature`

The provider layer (§11.7) is deliberately neutral: `agent.py` and `tools.py` see only
`Message`, `ToolCall` and `ToolSpec`, and no LLM SDK is imported outside
`app/buyer/llm/`. That is invariant 11, and test 22 enforces it.

Gemini 3 requires that the `thought_signature` attached to a `functionCall` part comes
back **inside its original Part** on the next turn. A layer that rebuilds conversation
history from `(name, arguments)` alone throws that field away by construction — and the
next request returns a 400. The neutrality that makes the architecture claim checkable
is exactly what broke the integration.

The fix keeps both: `ToolCall` and `Message` carry a `provider_metadata: dict` that the
adapter which produced the call writes and only that same adapter reads. It is opaque
and JSON-serialisable so a cassette can record it. Nothing above `app/buyer/llm/` reads,
interprets or depends on its contents, and `base.py` does not know what any provider
puts in it. The cassette request hash deliberately excludes it, because per-call
transport state is not part of the logical request.

### The container running stale code

`docker compose up -d` reuses the existing image. It does not rebuild it. The container
silently ran M4 code for several hours during M5 while I debugged a Guard that was, in
the running process, not there at all. **Always `docker compose up -d --build`.** It is
in the quickstart above for that reason.

Two neighbours of the same mistake, for anyone picking this up: `git commit -am` stages
only files git already tracks, and every milestone creates new ones — use `git add -A`.
And commit ≠ push ≠ deploy; after pushing, confirm the route is actually live with
`curl -s $BASE/openapi.json | grep <new-route>`.

---

## Deviations from BUILD_SPEC

Seven, all deliberate, all noted in the code at the point they apply.

| # | Deviation | Why |
|---|---|---|
| 1 | **Wall clock is 300 s, not §11.4's 120 s.** | Gemini takes ~30 s per call and a full run needs ~220 s; 120 s cannot reach authorization. `MAX_TOOL_CALLS = 20` and `MAX_SUBMIT_ATTEMPTS = 2` are untouched — those bound what the agent can *do*, and the wall clock only bounds how long it may take to do it. |
| 2 | **`provider_metadata` and `transport_wait_seconds` added to the §11.7 dataclasses.** | `provider_metadata` is the Gemini 3 `thought_signature` fix above. `transport_wait_seconds` credits provider-mandated retry waits back to the wall-clock budget: a session sitting on a rate-limit delay the provider itself asked for is not a stuck session, and killing it would report a limit breach for time the agent never got to use. Nothing above `llm/` reads either field. |
| 3 | **The browser never receives `MERCHANT_API_KEY`.** | §14 says the frontend polls `GET /merchant/orders/{id}`, but that route requires the merchant API key, and shipping it in a JS bundle would let any visitor act as the agent — a credential leak dressed as a feature. `/api/ui/orders/{id}` delegates to the same `merchant.service.get_order()`: same truth, less authority. |
| 4 | **`/api/ui/config` serves `RAZORPAY_KEY_ID` at runtime** rather than baking it into the bundle. | The key id is public by design, but a runtime read means a judge can clone the repo, drop in their own key, and rebuild nothing. |
| 5 | **`BLOCKED → DISCOVERING` added to the §11.3 transition table.** | After a block the agent should re-plan. Discovery reads a public registry, takes no arguments and grants no authority, so allowing it back is safe — and without it a blocked session halts instead of recovering. |
| 6 | **M7 was built on live Gemini, not cassette replay.** | Cassette replay cannot run the frontend end to end; see the known gap below. The decision was to build the console against a live model rather than build a tool-result substitution layer the spec does not ask for. |
| 7 | **`propose_mandate` takes `{quote_id, justification}` — no amount.** | §11.2 forbids a monetary parameter on any tool. Limits are derived server-side from the signed quote, so no sum ever crosses from the model. The agent cannot propose authority larger than the thing it is proposing to buy. |

One structural note that is not really a deviation: §3 shows a single `app/platform/demo.py`.
It would have been ~600 lines, so it is an assembler over one-concern-per-file modules
beside it (`demo_merchant`, `demo_authority`, `demo_webhook`, `demo_reset`,
`demo_injection`, `demo_evidence`, over a shared `demo_base` and `demo_scenario`). Every
route still mounts under `/api/demo` and every route is still gated by `DEMO_MODE`.

---

## Known gaps

Written down deliberately. A known gap is worth more than an unknown one that happens to
be absent.

- **The validator reads `quote.signing_payload` without verifying `quote.signature`.**
  CV-002 and CV-003 iterate the signed line items but take them on trust. Not
  exploitable in this build — the merchant issued the quote and the row is its own — and
  the Guard's MG-008 does verify that signature before any submit can happen. It is
  still an inconsistency: the merchant plane is supposed to be able to stand alone.
- **MG-007 reads `category` from the display column, not from a signed field.** §6.3
  deliberately excludes category from the quote signing payload, so this one rule reads
  unsigned data. The correct fix is to derive the category from `products` at evaluation
  time rather than to widen what gets signed. Changing the signed payload this late
  would invalidate every quote and signature already captured in the audit trail, so it
  was not done.
- **Cassette replay cannot run end to end.** Replay serves recorded *model* responses,
  but the tool layer still executes for real, and the `cart_id` and `quote_id` values in
  a recording do not exist in a fresh database. §11.8's cassette schema stores no tool
  results to remap from. The decision taken was **not** to build a substitution layer —
  it would be a large piece of machinery serving only the demo. Cassettes are used where
  the tool layer is mocked and the problem does not arise.
- **Idempotent replay does not detect an `Idempotency-Key` collision across different
  quotes.** §7.8 mandates "do not re-validate" on a repeated key, so a replay returns
  the original order without looking at what was submitted with it. This is intended
  behaviour rather than a bug, but it means the key is trusted to be unique.

---

## Two things to know about this build

**All three seed merchants share one signing key.** Protein Kitchen, Nova Stationery and
Saffron Tiffin all publish the public half of `MERCHANT_SIGNING_SEED`. In a real
deployment each merchant would hold its own key and the buyer would fetch each one from
that merchant's `/.well-known/ucp`. The verification code already works that way — it
reads the key from the profile — but there is only one key behind the three profiles
here. This is a seeding simplification, not an architectural one.

**Cassettes are demo insurance, not fakery.** `CASSETTE_MODE=record|replay` (§11.8)
records real model responses to `cassettes/` and replays them offline. A replayed run
records `llm_provider = "cassette"` on the session row and in the audit trail, so the
trail always says which it was. They exist so a rate limit or a dead network cannot kill
a live demo, and so the test suite can run at zero cost. They are never presented as a
live run.

---

## Repository map

```
app/buyer/       the agent, its ten tools, and the HTTP-only merchant client
app/buyer/llm/   the only files that import an LLM SDK
app/merchant/    discovery, carts, signed quotes, the CV-001..CV-004 validator
app/platform/    the Mandate Authority, the Transaction Guard, payments, webhooks, demo
app/audit.py     the only code that writes audit_events
tests/           the 26 tests of §17 plus four additions
scripts/         the attack-proof scripts named above
BUILD_SPEC.md    the specification. Single source of truth, 23 sections.
CLAUDE.md        standing orders for the coding agent
```

Every rule id, block code, error code and event type string in this README is the one
that appears in the API response, the audit payload and the guard console. Never a
near-miss.
