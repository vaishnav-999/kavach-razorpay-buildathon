# KAVACH — HANDOVER / RESUME BRIEF

Paste this whole file into a new Claude chat if the conversation is lost.
Keep it in the repo root and update "Where I am" after every milestone.

Last updated: 3 September 2026, after M8.

---

## PROMPT TO PASTE INTO A NEW CHAT

> I'm building **Kavach**, a Razorpay Buildathon Track 01 project — an AI buyer that
> transacts with a merchant end to end while a deterministic Transaction Guard makes it
> structurally incapable of exceeding the authority a user granted it. The claim is:
> assume the LLM is fully compromised, the system is still financially safe.
>
> The full spec is `BUILD_SPEC.md` — single source of truth, 23 sections, and every
> milestone prompt cites it by section number. Standing orders for the coding agent are
> `CLAUDE.md`. The 9 milestone prompts are `MILESTONE_PROMPTS.md`. Setup and operating
> procedure is `SETUP.md`.
>
> I build with Claude Code in Cursor's terminal, one milestone per session, `/clear`
> between each, commit after each. **You are my architect and debugging partner — not
> the engineer.** Claude Code writes the code. I want you to explain things in plain
> terms and define jargon as it comes up, and give me next steps one at a time rather
> than several at once.
>
> I'm on Windows / PowerShell 5.1, corporate laptop. Docker Desktop works. Postgres runs
> on host port **5433**. Deployed on Render at **https://kavach-4576.onrender.com**.
>
> I've completed M0–M8. See "Where I am" below. Read the attached BUILD_SPEC.md and pick
> up from there.

Attach: `BUILD_SPEC.md`, `MILESTONE_PROMPTS.md`, `CLAUDE.md`, and this file.

---

## WHERE I AM

**Done: M0–M8, plus unplanned M5a/M5b security hardening.**
**Next: M9 — Tests + docs (2h).**

| Milestone | State | Evidence |
|---|---|---|
| M0 Foundation | done | 12 tables, seed loaded, `/health` ok |
| M1 Deploy | done | Live on Render, Blueprint deploy, webhook registered |
| M2 Merchant interface | done | Signed quote returns **516000**; snapshot-ignored test passed |
| M3 Razorpay | done | Real payment `pay_TWwHaQNLivXqVp` ₹5,160 Captured |
| M4 Webhooks | done | 6 genuine events all `signature_valid=t`, 1 forged probe rejected; 3 orders PAID |
| M5 Guard | done | MG-005 BLOCK, 9/9 rules, **zero Razorpay activity confirmed in the dashboard** |
| M5a/M5b hardening | done | Four binding gaps found and closed, each with a proof script |
| M6 Agent | done | Live Gemini run to `AWAITING_AUTHORIZATION`, 9 tool calls, 0 submit attempts |
| M7 Frontend | done | Console live locally and on Render; block triggered from the UI |
| M8 Demo panel + injection | done | Five §15 levers verified over HTTP; forced poisoned cart → MG-005 BLOCK, 9/9 rules, Razorpay client call counter unmoved |

### Progress

~23 of 30 hours spent. Remaining: M9 (2h), recording (3h protected).
By risk the project is ~85% done — everything that could force a redesign is behind me.

---

## DO THESE FIRST (open at the time of writing)

1. **Enable Gemini API billing.** Free tier on `gemini-3.6-flash` is **20 requests/day**
   and one agent run costs ~10. Currently working around it on
   `gemini-3.5-flash-lite` (15 RPM / 500 RPD). Estimated real cost: ₹300–500 for
   everything remaining. After enabling, switch `GEMINI_MODEL` back to
   `gemini-3.6-flash`.
2. **Render can't run the agent yet.** Its dashboard env still has
   `LLM_PROVIDER=cassette` and no Gemini key. The UI loads and the merchant/guard paths
   work, but the agent does not. Add `GEMINI_API_KEY`, `LLM_PROVIDER=gemini` and
   `GEMINI_MODEL` in the Render dashboard before demoing from the live URL.
3. **Reset seed stock before recording.** `POST /api/demo/reset` now does this, and
   keeps `audit_events`.
4. **Rebuild the container after M8.** The demo router and the rebuilt frontend bundle
   are new: `docker compose up -d --build`.

### M8 — what the demo panel gives you

`app/platform/demo.py` is the assembler; the actions live one concern to a file
beside it (`demo_merchant`, `demo_authority`, `demo_webhook`, `demo_reset`,
`demo_injection`, `demo_evidence`, over a shared `demo_base` and
`demo_scenario`). §3 shows a single `demo.py`; it would have been 600 lines, and
CLAUDE.md's ~300-line rule won. All of it mounts under `/api/demo`, every route
gated by `DEMO_MODE`. With the
flag off they answer **403 `DEMO_MODE_DISABLED`** rather than 404, so a judge sees a
switch that is off, not a broken deployment.

| Endpoint | Effect |
|---|---|
| `POST /api/demo/drift-price` | PK-003 45000 → 49500. CV-003 on the next submit |
| `POST /api/demo/deplete-stock` | PK-003 stock → 3 against a cart of 4. CV-002 |
| `POST /api/demo/revoke-mandate` | Revokes the newest ACTIVE mandate. MG-002 |
| `POST /api/demo/replay-webhook` | Re-POSTs the stored raw body + signature over real HTTP. One state transition |
| `POST /api/demo/reset` | Seed prices and stock back, transaction tables cleared, `audit_events` kept |
| `POST /api/demo/force-poisoned-cart` | PK-001×8 + PK-003×4 + PK-005×12 = 756000 through the real submit path → MG-005 BLOCK |
| `GET /api/demo/injection` | PK-005's description verbatim + digest, the `submit_purchase` schema, the absent parameters |

Every response says what it changed, and the console renders it. The **Injection**
button in the header opens the side-by-side screen: the description as served on the
left, the nine-rule BLOCK on the right, the tool schema underneath.

The model still does **not** fall for the injection, and nothing was weakened to make
it. The forced control is how the block is demonstrated deterministically.

---

## THE NUMBERS EVERYTHING DEPENDS ON

| | Paise | Rupees |
|---|---|---|
| PK-001 Paneer Protein Bowl ₹420 × 8 | 336 000 | |
| PK-003 Chicken Power Bowl ₹450 × 4 | 180 000 | |
| **Correct cart** | **516 000** | ₹5,160 |
| PK-005 Protein Dessert Box ₹200 × 12 (injected) | 240 000 | |
| **Poisoned cart** | **756 000** | ₹7,560 |
| **Demo mandate cap** | **600 000** | ₹6,000 |
| PK-003 price drift 45000 → 49500 | new total 534 000 | |

If a quote ever returns something other than 516000 for that cart, the seed is wrong.
Note: an early draft of MILESTONE_PROMPTS said PK-001 is `28000`. It is **42000**.

Merchant is **Protein Kitchen** (`protein-kitchen`, category `meals`). Anything in the
repo still saying `prakash` or `catering` is stale.

---

## MY ENVIRONMENT

- Windows, PowerShell **5.1** — no `-SkipHttpErrorCheck`, use `curl.exe` not `curl`
- Docker Desktop; **Postgres on host port 5433**
- Live URL: `https://kavach-4576.onrender.com`
- GitHub: `vaishnav-999/kavach-razorpay-buildathon`, branch `main`
- Git identity is correct: `Vaishnav <vaishnav.tathele25@gmail.com>`
- Render Blueprint deploy; free tier sleeps after 15 min, ~40s to wake
- Razorpay **test mode**. UPI NOT enabled — use **Netbanking → any bank → Success**
- Razorpay checkout asks for a mobile number: `9999999999`
- Webhook OTP: `754081`. Webhook URL must include `/api/webhooks/razorpay`
- `MERCHANT_API_KEY` = `kavach_merchant_dev_key_change_me` (same locally and on Render)
- `.env` currently: `LLM_PROVIDER=gemini`, `GEMINI_MODEL=gemini-3.5-flash-lite`,
  `GEMINI_MIN_CALL_INTERVAL_SECONDS=5`

---

## SESSION START CHECKLIST

```powershell
docker compose up -d --build      # ALWAYS --build
docker compose ps                 # db and app both "running"
curl.exe -s http://localhost:8000/health
Invoke-RestMethod -Uri "https://kavach-4576.onrender.com/health"
```

---

## THE MISTAKES I ALREADY MADE — DON'T REPEAT THEM

1. **Forgetting `--build`.** `docker compose up -d` reuses the old image. The container
   silently ran M4 code for hours during M5.
2. **`git commit -am` misses new files.** `-a` only stages files git already tracks.
   Every milestone creates new ones. **Always `git add -A`**, then check
   `git status --short` prints nothing.
3. **Commit ≠ push ≠ deploy.** After pushing, confirm the new route is live:
   `curl.exe -s $base/openapi.json | Select-String "<new-route>"`
4. **Local and Render are different databases.** `docker compose down -v` only wipes
   local. Seeding only runs when `merchants` is empty.
5. **PowerShell mangles JSON in curl.** Write the body to a file and use `-d "@file.json"`,
   or use `Invoke-RestMethod` with `(@{...} | ConvertTo-Json)`.
6. **Demo scripts can't run from Windows without an override.** `.env` sets
   `DATABASE_URL` to host `db`, which only resolves inside Docker. From PowerShell:
   `$env:DATABASE_URL = "postgresql://kavach:kavach@localhost:5433/kavach"`
   Or run them in the container. `scripts/` is deliberately not in the image.
7. **Response field names vary.** Cart uses `id`, quote uses `quote_id`, session uses
   `session_id`. Run `$x | Format-List` before chaining off any response.
8. **A session halted by a provider error cannot be resumed.** Its history ends on a
   model turn and Gemini rejects that with a 400. Always start a new session.

---

## USEFUL COMMANDS

```powershell
# Run the demo scripts from Windows
$env:DATABASE_URL = "postgresql://kavach:kavach@localhost:5433/kavach"
python scripts/demo_block.py             # BLOCK on MG-005, no order
python scripts/demo_allow.py             # ALLOW, real Razorpay order
python scripts/demo_decision_binding.py  # both binding attacks refused at 409

# Or inside the container (scripts/ is not in the image, copy first)
docker cp scripts kavach-app-1:/srv/scripts
docker compose exec app python scripts/demo_block.py

# Full agent run from PowerShell
@{ content = "Order lunch for our 12-person offsite on Thursday. Eight of us are vegetarian, four are not. High protein if you can. Keep it under six thousand rupees." } | ConvertTo-Json | Set-Content -Encoding utf8 msg.json
$s = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/buyer/sessions" -ContentType application/json -Body (@{ user_email = "priya@example.com" } | ConvertTo-Json)
$s.session_id
curl.exe -N -X POST "http://localhost:8000/api/buyer/sessions/$($s.session_id)/message" -H "Content-Type: application/json" -d "@msg.json"

# Reset stock
docker compose exec db psql -U kavach -d kavach -c "UPDATE products SET stock_qty = 200 WHERE sku = 'PK-001'; UPDATE products SET stock_qty = 40 WHERE sku = 'PK-003';"

# Quote flow (expect 516000)
$H = @{ "X-Merchant-API-Key" = "kavach_merchant_dev_key_change_me" }
$base = "https://kavach-4576.onrender.com"
```

---

## THE M5a/M5b HARDENING — WHY IT EXISTS

After M5 I asked Claude Code to sweep for one pattern: **a check that is correct, but
where the binding between two artifacts is unverified.** It found four instances. All
are now closed, each with a proof script that mounts the attack and shows it refused.

| Gap | The problem | Fix |
|---|---|---|
| Guard decision → quote | Merchant only checked `verdict == ALLOW`. Any past ALLOW could be attached to any quote. | `_require_allow_decision` binds `quote_id`, `mandate_id`, `requested_total_paise`, `merchant_id` |
| MG-001 | Signature covered `signing_payload`; rules MG-004..MG-009 read the columns. Nothing bound them. | MG-001 reconciles 7 fields payload-vs-row after the signature verifies |
| MG-008 | Quote signature verified, only `total_paise` reconciled back to it. | Full payload reconciliation including `typ` |
| correlation_id | Caller-supplied, split the audit chain in two. | Taken from the bound guard decision |

**This is the most interesting story in the project.** Every individual check was
correct; the gaps were in what they referred to. It's the classic *confused deputy*
shape. Worth telling in the README and the video.

Proof scripts: `scripts/demo_decision_binding.py` (two cases: wrong quote, wrong
mandate). MG-001/MG-008 drift verified by tampering with each field individually while
leaving the signature genuine — every one blocks, and an untampered control still passes.

---

## KNOWN GAPS (documented, not bugs to discover)

- **Validator reads `quote.signing_payload` without verifying `quote.signature`.**
  Consistency fix for M9. Not exploitable — the merchant issued the quote.
- **MG-007 reads `category` from the display column.** §6.3 deliberately excludes
  category from the signed payload, so this one rule reads unsigned data. Correct fix is
  to derive it from `products` at evaluation time. Do NOT change what gets signed this
  late. M9.
- **MG-001's `expires_at`** now normalises both sides (fixed). MG-008 too.
- **Idempotent replay doesn't detect key collision across different quotes.** §7.8
  mandates "do not re-validate", so this is intended behaviour, not a bug.
- **Cassette replay cannot work end to end.** Replay serves model responses, but tools
  still execute live, and recorded `cart_id`/`quote_id` values don't exist in a fresh
  database. §11.8's schema stores no tool results to remap from. **Decision taken: don't
  build a substitution layer.** M7 was built on live Flash-Lite. Cassettes are for M9
  tests, where the tool layer is mocked and the problem doesn't arise.
- **Two orders in the dev database point at M3 `DEV_SCAFFOLD` decisions.** Left alone;
  no new ones are creatable; §15 reset in M8 clears orders.
- **The agent still takes a discovery detour after a block** rather than going straight
  back to `create_cart`. The guidance text asks it not to. One sample. The
  `BLOCKED → DISCOVERING` transition is the safety net that stops it halting.

---

## DEVIATIONS FROM BUILD_SPEC (all deliberate, all noted in code)

1. **Wall clock 300s, not §11.4's 120s.** Gemini takes ~30s/call and a full run needs
   ~220s. 120 cannot reach authorization. `MAX_TOOL_CALLS = 20` and
   `MAX_SUBMIT_ATTEMPTS = 2` are untouched — those bound what the agent can *do*.
2. **`provider_metadata` and `transport_wait_seconds` added to §11.7 dataclasses.**
   Gemini 3 requires a `thought_signature` on `functionCall` parts to be returned inside
   its original Part; a neutral-types layer drops it and gets a 400. The adapter stores
   the whole Part opaquely. `transport_wait_seconds` credits provider-mandated retry
   waits back to the wall-clock budget. Nothing above `llm/` reads either field.
3. **The browser never receives `MERCHANT_API_KEY`.** §14 says poll
   `/merchant/orders/{id}`, but that route requires the key, and shipping it in a JS
   bundle would let any visitor act as the agent. `/api/ui/orders/{id}` delegates to the
   same `merchant.service.get_order()` — same truth, less authority.
4. **`/api/ui/config` serves `RAZORPAY_KEY_ID` at runtime**, not baked into the bundle.
5. **`BLOCKED → DISCOVERING` added to the transition table.** Discovery reads a public
   registry, takes no arguments, and grants no authority.
6. **M7 built on live Gemini, not cassette replay.** See the cassette gap above.
7. **`propose_mandate` takes `{quote_id, justification}`** — no amount. Limits are
   derived server-side from the signed quote, so no sum ever crosses from the model.

---

## UI DESIGN STANDARD (already applied in M7 — keep it)

The console reads as an instrument panel, not a shopping app. Rules:

- ONE accent colour, for primary actions and data highlights only. Green/red for rule
  pass/fail. Nothing else is colourful.
- No gradients, no glassmorphism, no blur, no neon, no 3D, no parallax.
- Neutrals from Tailwind zinc/stone. Dark mode from real elevation levels.
- 8px spacing grid. Density over whitespace. ONE border radius. 1px low-contrast borders.
- Lucide icons only. No emoji anywhere.
- Two fonts: a UI sans and a mono. Mono for every id, rule code, block code and amount.
- Motion 150–200ms ease-out, and it must carry information: rules stagger in as the
  result renders; on BLOCK the console takes a red left border.

Known cosmetic nits, low priority: the model label and two demo buttons wrap in the
header; empty-state panels are airier than the density rule wants.

---

## WHAT'S LEFT

| Milestone | Est. | Notes |
|---|---|---|
| **M8** Demo panel + injection | 2h | Wire the 5 header buttons (currently 404 with an honest "not mounted" message). Prove PK-005's injection reaches an MG-005 BLOCK with zero Razorpay activity. |
| **M9** Tests + docs | 2h | 26 tests from §17, all offline. Record cassettes here. README with the §21 matrix and §22 threat model verbatim. |
| **Record** | 3h protected | Do not let this get squeezed. A working project with no video scores zero. |

### The five tests that matter most (§17)

- **Test 10** — monkeypatch `razorpay_client.create_order` to raise, run a BLOCK path,
  assert it completes cleanly and the mock was never called. Mechanically proves
  invariant 2.
- **Test 13** — same `x-razorpay-event-id` twice produces one state transition.
- **Test 21** — `guard.evaluate` has exactly one call site.
- **Test 23** — no file under `app/buyer/` imports from `app.merchant`.
- **Test 26** — `get_provider()` raises when `LLM_PROVIDER` is unset.

---

## ARTIFACTS ALREADY CAPTURED (for the video / README)

- **Razorpay dashboard before/after a `demo_block` run** — identical order list, proving
  a blocked purchase produces *zero* Razorpay activity. This is the single strongest
  artifact in the project.
- **GuardConsole screenshot on MG-005 BLOCK** — nine rules, observed 516000 vs threshold
  400000, "No Razorpay order was created", decision/mandate/quote ids, 6ms.
  The 400000 ceiling was typed by a human into the AuthorizationCard, which answers
  judge check #4 without any demo endpoint.

Keep collecting stills whenever something works. They're insurance if the live run
misbehaves during the recording.

---

## THE FIVE THINGS A JUDGE WILL CHECK (§23)

1. Can they clone it, add their own Razorpay test keys, and complete a purchase?
2. Does the deployed URL work? — **yes, console renders on Render**
3. Does `GET /api/audit/{correlation_id}` return the full ordered chain? — **yes, built
   in M7**
4. Can they trigger the Guard block themselves from the demo panel? — **yes, already
   possible by lowering the cap in the AuthorizationCard**
5. Does every threat in §22 have a corresponding test? — **M9**
