# KAVACH — HANDOVER / RESUME BRIEF

Paste this whole file into a new Claude chat if this conversation is lost.
Keep it in the repo root and update the "Where I am" section after each milestone.

---

## PROMPT TO PASTE INTO A NEW CHAT

> I'm building **Kavach**, a Razorpay Buildathon Track 01 project — an AI buyer that
> transacts with a merchant end to end while a deterministic Transaction Guard makes it
> structurally incapable of exceeding the authority a user granted it. The claim is:
> assume the LLM is fully compromised, the system is still financially safe.
>
> The full spec is `BUILD_SPEC.md` in my repo — it is the single source of truth, 23
> sections, and every milestone prompt cites it by section number. Standing orders for
> the coding agent are `CLAUDE.md`. The 9 milestone prompts are `MILESTONE_PROMPTS.md`.
> Setup and operating procedure is `SETUP.md`.
>
> I build with Claude Code in Cursor's terminal, one milestone per session, `/clear`
> between each, commit after each. You are my architect and debugging partner — not the
> engineer. Claude Code writes the code.
>
> I'm on Windows / PowerShell 5.1, corporate laptop. Docker Desktop works. Postgres runs
> on host port **5433** (5432 was taken). Deployed on Render at
> **https://kavach-4576.onrender.com**.
>
> I've completed M0–M4. See "Where I am" below. Read the attached BUILD_SPEC.md and pick
> up from there.

Attach: `BUILD_SPEC.md`, `MILESTONE_PROMPTS.md`, `CLAUDE.md`, and this file.

---

## WHERE I AM

**Done: M0, M1, M2, M3, M4.** Next: **M5 — Mandate + Transaction Guard + Validator (4h+).**

| Milestone | State | Evidence |
|---|---|---|
| M0 Foundation | done | 12 tables, seed loaded, `/health` ok |
| M1 Deploy | done | Live on Render, Blueprint deploy, webhook registered |
| M2 Merchant interface | done | Signed quote returns **516000**; snapshot-ignored test passed |
| M3 Razorpay | done | Real payment `pay_TWwHaQNLivXqVp` ₹5,160 Captured in dashboard |
| M4 Webhooks | done | 6 genuine events, all `signature_valid=t`; 3 orders `PAID`/516000 |

**Verified working end to end:** cart → signed quote → Razorpay order → real test payment
→ server-side signature verification → webhook received and deduped.

**Seed correction already applied:** the merchant is **Protein Kitchen** (`protein-kitchen`,
category `meals`), NOT "Prakash Kitchen". An early draft of BUILD_SPEC had the wrong name.
If anything in the repo still says `prakash`/`catering`, it is stale.

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

---

## MY ENVIRONMENT

- Windows, PowerShell **5.1** — no `-SkipHttpErrorCheck`, use `curl.exe` not `curl`
- Docker Desktop working; **Postgres on host port 5433**
- Live URL: `https://kavach-4576.onrender.com`
- GitHub: `vaishnav-999/kavach-razorpay-buildathon`, branch `main`
- Render Blueprint deploy; free tier sleeps after 15 min, ~40s to wake
- Razorpay **test mode**. UPI is NOT enabled — use **Netbanking → any bank → Success**
- Razorpay checkout asks for a mobile number: `9999999999`
- Webhook OTP: `754081`
- Webhook URL must include the path: `/api/webhooks/razorpay`
- `MERCHANT_API_KEY` = `kavach_merchant_dev_key_change_me` (same locally and on Render)

---

## SESSION START CHECKLIST

```powershell
# 1. containers up, WITH a rebuild if code changed
docker compose up -d --build
docker compose ps          # both db and app "running"

# 2. re-set variables (they vanish with the window)
$H = @{ "X-Merchant-API-Key" = "kavach_merchant_dev_key_change_me" }
$base = "https://kavach-4576.onrender.com"

# 3. confirm both sides alive
curl.exe -s http://localhost:8000/health
Invoke-RestMethod -Uri "$base/health"
```

---

## THE FOUR MISTAKES I ALREADY MADE — DON'T REPEAT THEM

1. **Forgetting `--build`.** `docker compose up -d` reuses the old image. New code never
   enters the container. Always `docker compose up -d --build` after a milestone.
2. **Forgetting to commit and push.** M4 tested as broken for an hour because it was never
   pushed to Render. **After every milestone: `git status --short` must be empty, then
   confirm the new route exists live** —
   `curl.exe -s $base/openapi.json | Select-String "<new-route>"`
3. **Local and Render are two different databases.** `docker compose down -v` only wipes
   local. Seeding only runs when `merchants` is empty, so a seed change on Render needs the
   tables cleared and the service restarted.
4. **PowerShell mangles JSON in curl.** Use `Invoke-RestMethod` with
   `(@{...} | ConvertTo-Json)`, or use `$base/docs` in a browser.

---

## USEFUL COMMANDS

```powershell
# quote flow (expect 516000)
$cart = Invoke-RestMethod -Method Post -Uri "$base/merchant/carts" -Headers $H `
  -ContentType application/json -Body (@{ merchant_id = "mch_pk0000000000" } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$base/merchant/carts/$($cart.id)/items" -Headers $H `
  -ContentType application/json -Body (@{ sku = "PK-001"; qty = 8 } | ConvertTo-Json) | Out-Null
Invoke-RestMethod -Method Post -Uri "$base/merchant/carts/$($cart.id)/items" -Headers $H `
  -ContentType application/json -Body (@{ sku = "PK-003"; qty = 4 } | ConvertTo-Json) | Out-Null
$quote = Invoke-RestMethod -Method Post -Uri "$base/merchant/checkout/quote" -Headers $H `
  -ContentType application/json -Body (@{ cart_id = $cart.id } | ConvertTo-Json)
$quote.total_paise

# NOTE: the quote's id field is `quote_id`, not `id`. The cart's IS `id`.
# When chaining off any response, run `$x | Format-List` first.

# connect to the RENDER database (get current password from Render → kavach-db)
docker compose exec -e PGPASSWORD=<pw> db psql -h <host>.singapore-postgres.render.com -U kavach <dbname>
```

---

## WHAT'S LEFT

| Milestone | Est. | Notes |
|---|---|---|
| **M5** Mandate + Guard + Validator | 4h (expect 5–6) | The project. 9 rules MG-001…MG-009, 4 validator rules, idempotent submit, single guard call site. Deletes the M3 dev scaffold. |
| **M6** The agent | 3h (expect 4–5) | LLM provider layer, 10 tools, state machine. **Record 3 cassettes at the end.** |
| **M7** Frontend | 3h | Run on `LLM_PROVIDER=cassette`, `CASSETTE_MODE=replay` — zero token cost. |
| **M8** Demo panel + injection | 2h | PK-005 injection must reach MG-005 BLOCK with zero Razorpay activity. |
| **M9** Tests + docs | 2h | 26 tests, all offline. README with §21 matrix and §22 threat model verbatim. |
| **Record** | 3h protected | Do not let this get squeezed. A working project with no video scores zero. |

Before M6: switch `LLM_PROVIDER` from `cassette` to `gemini` and set `GEMINI_API_KEY` and
`GEMINI_MODEL` in `.env` and on Render. Pick the newest **Flash** model with a free tier —
check `ai.google.dev/gemini-api/docs/models` on the day; the free-tier rows change.

---

## M5 VERIFY — what "done" looks like

Ask Claude Code for `scripts/demo_block.py`: builds an over-budget cart, issues a ₹6,000
mandate, submits. You want:

- verdict `BLOCK`
- `failed_rule_id: MG-005`
- **all nine rules** present, each with observed / threshold / detail — passing ones too
- **nothing new in the Razorpay dashboard**

That last line is the whole project. A blocked purchase produces zero Razorpay activity —
not a cancelled order, not a failed payment, nothing at all.
