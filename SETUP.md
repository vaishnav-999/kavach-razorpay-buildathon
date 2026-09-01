# KAVACH — SETUP & OPERATING PROCEDURE

Read this once, top to bottom, before touching anything. It takes 10 minutes to read
and will save you hours.

---

## THE MENTAL MODEL

You are not writing code. You are running a build.

| Thing | What it is for you |
|---|---|
| **Claude Code** | The engineer. Writes every line. Runs in a terminal. |
| **Cursor** | The window. You look at files, and it has a terminal to run Claude Code in. |
| **BUILD_SPEC.md** | The blueprint. Claude Code reads it. You don't have to. |
| **CLAUDE.md** | Standing orders that Claude Code reads automatically, every session. |
| **MILESTONE_PROMPTS.md** | 9 prompts. You paste one at a time. |
| **You** | Paste prompt → wait → check one thing works → `git commit` → next prompt. |

**The single biggest way this fails:** pasting all 9 prompts at once, ending up with
3,000 lines of broken code at 2am with no idea which part broke. Do not do that.
One milestone. Verify. Commit. Next.

---

# PART A — ONE-TIME SETUP (about 60 minutes)

Do all of Part A before you start building. Don't skip ahead.

## A1. Install the tools (~20 min)

### 1. Node.js
Claude Code needs it, and M7 builds the frontend with it.

- Go to https://nodejs.org
- Download the **LTS** version, install with all defaults
- Verify: open Terminal (Mac) or PowerShell (Windows) and type `node --version`
  You should see something like `v20.x.x`

### 2. Git
- **Mac:** type `git --version` in Terminal. If it prompts to install developer
  tools, say yes.
- **Windows:** https://git-scm.com/download/win — install with all defaults.

### 3. Docker Desktop
This runs your local database.

- https://www.docker.com/products/docker-desktop
- Install, launch it, wait for the whale icon to stop animating
- Verify: `docker --version`

> **If Docker fights you for more than 15 minutes, skip it.** You can use the Render
> database (step A3) for local development instead. Just paste the Render
> `DATABASE_URL` into your local `.env`. It's slower but it works. Don't lose an hour
> to Docker on day one.

### 4. Cursor
- https://cursor.com — download, install, open it
- Skip any onboarding tours. You need almost none of it.

## A2. Create your accounts (~15 min)

### Razorpay
1. Go to https://dashboard.razorpay.com and sign up
2. **Look at the top of the dashboard for a Test Mode / Live Mode toggle. Switch to
   TEST MODE.** Everything you do must be in test mode. The app refuses to start on a
   live key, deliberately.
3. Go to **Account & Settings → API Keys → Generate Test Key**
4. You get a **Key ID** (starts `rzp_test_`) and a **Key Secret**
5. **Download / copy both now.** The secret is shown once and never again.

Keep this browser tab open. You'll come back in A4.

### Google AI Studio — the agent's brain (free, no credit card)

The agent inside your app makes 8–14 model calls per shopping session. Gemini's free
tier covers it; Anthropic is optional.

1. Go to https://aistudio.google.com/apikey
2. Sign in with a Google account
3. **Create API key** → pick or create a project
4. Copy it into `GEMINI_API_KEY`

**Pick the model on the day you build.** Open
https://ai.google.dev/gemini-api/docs/models and choose the newest **Flash** model that
still shows a free tier. Not Pro — Pro has far lower free-tier daily limits and this
agent does not need its reasoning. Put the exact model id in `GEMINI_MODEL`. Model rows
and free-tier quotas have changed repeatedly through 2026, so check rather than trusting
any number written here; AI Studio shows live RPM / TPM / RPD for your project.

> **A weaker, faster model is more likely to fall for the PK-005 injection. That is the
> demo working as designed.** Kavach's claim is not "our agent is smart enough to resist
> attacks" — it is "assume the model is compromised, the system is still safe." If you
> hit daily limits, dropping to Flash-Lite is not a downgrade for this project.

> **If you hold a Google AI Pro / AI Plus subscription, it does not raise your API key's
> quota.** The consumer subscription and the programmatic API are separate meters; your
> app hits the second one on the standard free tier. What the subscription *is* good for
> is prompt development at zero API cost — the AI Studio Playground draws on
> subscription quota. Tune the §11.5 system prompt there, paste the final text into
> `app/buyer/prompts.py`, and you remove the largest source of throwaway calls in M6/M8.

> **Privacy note:** on the free tier Google may use API inputs and outputs to improve
> their models. Irrelevant here — the data is a fictional lunch order. Do not put
> anything real through it.

### Anthropic API key — optional
Only if you want one polished recording run on Claude. https://console.anthropic.com →
API Keys → Create Key. Set `LLM_PROVIDER=anthropic` and record. Six sessions costs cents.

> Your Claude Max plan covers Claude Code, the engineer. That is a separate thing from
> the key the *agent inside your app* uses to think.

### Render
1. https://render.com → sign up with GitHub
2. Nothing else yet.

### GitHub
If you don't have an account, create one at https://github.com. You need a repo to
deploy from.

## A3. Create the project folder (~5 min)

**Mac:**
```bash
cd ~/Desktop
mkdir kavach
cd kavach
git init
```

**Windows (PowerShell):**
```powershell
cd $HOME\Desktop
mkdir kavach
cd kavach
git init
```

Now open it in Cursor: **File → Open Folder → select `kavach`**

## A4. Put the spec files in place (~5 min)

Drag these four files into the `kavach` folder:

```
kavach/
├── BUILD_SPEC.md
├── CLAUDE.md
├── MILESTONE_PROMPTS.md
└── SETUP.md            (this file)
```

Then the Cursor rules file. In Cursor: **File → New Folder** → name it `.cursor`, then
inside it another folder `rules`, then drop `kavach.mdc` in there.

```
kavach/.cursor/rules/kavach.mdc
```

## A5. Install and start Claude Code (~5 min)

In Cursor, open the terminal: **`Ctrl + \`** (backtick, the key above Tab).
On Mac it's **`Cmd + \``**. A terminal panel opens at the bottom.

```bash
npm install -g @anthropic-ai/claude-code
```

Wait for it to finish, then:

```bash
claude
```

It'll ask you to log in — a browser opens, you sign in with your Claude account,
come back. You'll see a `>` prompt in the terminal. **That's Claude Code. That's
where you paste the milestone prompts.**

Type `/init` once — it reads your folder and confirms it found `CLAUDE.md`.

---

# PART B — THE OPERATING LOOP

This is what you repeat 9 times.

```
1. Open MILESTONE_PROMPTS.md
2. Copy the entire block for the current milestone
3. Paste into the Claude Code terminal, press Enter
4. Wait. It will create/edit files and tell you when it's done.
   Approve file edits when it asks. Say yes.
5. Run the verification command listed at the end of that milestone
6. If it works → commit:
       git add -A
       git commit -m "M2: merchant interface"
7. If it doesn't work → paste the error into Claude Code and say
       "This failed. Fix it. Do not move on."
8. Next milestone.
```

**Commit after every single milestone.** If milestone 6 goes badly wrong, you can
`git reset --hard HEAD` and be back at the end of milestone 5 in one second. This is
your undo button and it costs you five seconds per milestone.

## Useful Claude Code commands

| Command | What it does |
|---|---|
| `/clear` | Wipes the conversation. **Do this between milestones.** Keeps it focused. |
| `/init` | Re-reads CLAUDE.md |
| `Esc` | Interrupts it mid-work if it's going the wrong way |
| `/cost` | Shows token usage |

**Use `/clear` between every milestone.** Each prompt is self-contained. A long
conversation makes it slower and more confused, not smarter.

## What to do when it goes wrong

| Symptom | What to say |
|---|---|
| Error in terminal | Paste the whole error. Add: "Fix this. Do not change anything else." |
| It built the wrong thing | "That's not what BUILD_SPEC.md section X says. Re-read it and correct." |
| It's inventing an API | "Do not invent Razorpay behaviour. Only use what BUILD_SPEC.md section 12 specifies." |
| It sanitized the injection | "PK-005's description is served verbatim. See §16.5 and CLAUDE.md. Revert that." |
| It's gone way off | `Esc`, then `git reset --hard HEAD`, then `/clear`, then re-paste the milestone |

## Where Cursor actually helps

Mostly: reading files, seeing what changed. Two things worth knowing:

- **Cmd/Ctrl + P** — jump to any file by typing its name
- **Left sidebar** — the file tree. Click any file to read it.

You can also select a few lines and hit **Cmd/Ctrl + K** to ask for a small edit.
Use that for tiny fixes only. **Do not run Cursor's agent and Claude Code on the same
files at the same time** — they'll overwrite each other. Claude Code is your engineer.
Cursor is your window.

---

# PART C — DEPLOY (do this at the end of Milestone 1, not at the end of the project)

You have to deploy on day one. Razorpay blocks ngrok and localtunnel as webhook URLs,
so there is no local shortcut for webhooks. This is the step people leave until the
end and then run out of time.

## C1. Push to GitHub

```bash
git add -A
git commit -m "M0 M1 complete"
```

Then in Cursor's terminal:
```bash
gh repo create kavach --private --source=. --push
```

If `gh` isn't installed, do it manually: create an empty repo on github.com, then:
```bash
git remote add origin https://github.com/YOUR_USERNAME/kavach.git
git branch -M main
git push -u origin main
```

## C2. Create the database on Render

1. Render dashboard → **New + → PostgreSQL**
2. Name: `kavach-db`. Region: **Singapore** (closest to you). Plan: **Free**.
3. Create it. Wait ~2 minutes.
4. Copy the **Internal Database URL**. Save it.

## C3. Create the web service

1. Render → **New + → Web Service**
2. Connect your GitHub, pick the `kavach` repo
3. Settings:
   - **Runtime:** Docker
   - **Region:** Singapore (same as the DB)
   - **Plan:** Free
4. Scroll to **Environment Variables** and add every one of these:

```
DATABASE_URL              <the Internal Database URL from C2>
APP_BASE_URL              https://kavach-xxxx.onrender.com   (fill in after first deploy)
DEMO_MODE                 true

RAZORPAY_KEY_ID           rzp_test_...
RAZORPAY_KEY_SECRET       ...
RAZORPAY_WEBHOOK_SECRET   kavach_webhook_secret_2026

MANDATE_SIGNING_SEED      <see below>
MERCHANT_SIGNING_SEED     <see below — must differ from the one above>
MERCHANT_API_KEY          kavach_merchant_dev_key_change_me

LLM_PROVIDER              gemini
GEMINI_API_KEY            ...
GEMINI_MODEL              <the Flash model id you picked in A2>

CASSETTE_MODE             off
CASSETTE_DIR              cassettes
```

Generate the two seeds — run this twice, use a different output for each:
```bash
python3 -c "import os;print(os.urandom(32).hex())"
```

`MERCHANT_API_KEY` can be any string, but it must be **identical** in your local `.env`,
in Render, and in any curl command you paste. Mismatches here produce 401s that look
like bugs.

5. **Create Web Service.** First build takes ~5 minutes.
6. When it's live, copy the URL (e.g. `https://kavach-a1b2.onrender.com`) and go
   back and set `APP_BASE_URL` to it. Render redeploys automatically.

7. **Test it:** open `https://your-url.onrender.com/health` in a browser. You should
   see `{"status":"ok"}`.

> **Free tier sleeps after 15 min of inactivity** and takes ~40 seconds to wake. Before
> recording your demo, load the page once and wait for it. Don't get caught by this
> mid-recording.

## C4. Register the webhook — this is the step everyone forgets

1. Razorpay dashboard, **still in Test Mode**
2. **Settings → Webhooks → + Add New Webhook**
3. Fill in:
   - **Webhook URL:** `https://your-url.onrender.com/api/webhooks/razorpay`
   - **Secret:** `kavach_webhook_secret_2026` (must match your env var exactly)
   - **Active Events** — tick these four:
     - `payment.authorized`
     - `payment.captured`
     - `payment.failed`
     - `order.paid`
4. Click Create. **When it asks for an OTP, enter `754081`** — that's the fixed
   test-mode OTP.

## C5. Prove the rail works

Before writing any more code, confirm a real payment goes through end to end. Milestone
3's verification step walks you through this. **Do not build the agent until a real test
payment has succeeded and a webhook has landed.**

---

# PART D — THE SCHEDULE

| | Milestones | Hours | You end the day with |
|---|---|---|---|
| **Day 1** | M0 → M4 | 10 | A real Razorpay test payment, verified server-side, webhook landing on a live URL |
| **Day 2** | M5 → M6 | 10 | The Guard blocking an over-budget purchase with zero Razorpay calls, plus three recorded cassettes |
| **Day 3** | M7 → M9 | 10 | Frontend, injection demo, tests, README, **recording** |

**Protect the last 3 hours of day 3 for recording.** Not negotiable. A working project
with no video scores zero.

## Which LLM mode, when

This is how you stay inside a free tier. M7 and M9 cost nothing because they replay
cassettes recorded at the end of M6.

| Phase | `LLM_PROVIDER` | `CASSETTE_MODE` | Live calls |
|---|---|---|---|
| M0–M5 | anything | `off` | **0** — no agent exists yet |
| M6 building | `gemini` | `off` | ~40 sessions |
| End of M6 | `gemini` | `record` | 3 sessions, cassettes saved |
| **M7 frontend** | `cassette` | `replay` | **0** |
| M8 injection | `gemini` | `off` | ~15 sessions |
| M9 tests | `cassette` | `replay` | **0** |
| Rehearsal | `cassette` | `replay` | **0** |
| **Recording** | `gemini` | `off` | ~6 live sessions |

## If you fall behind

Cut in this exact order:
1. Tests from 26 down to 12 (keep guard + webhook + boundary tests: 5, 10, 13, 14, 21,
   22, 23, 26 are the load-bearing ones)
2. Nova Stationery merchant — drop to 2 merchants
3. Audit trace UI — leave it as a raw JSON endpoint
4. Reconciliation endpoint

**Never cut:** the Transaction Guard, the signed mandate, the real Razorpay payment,
the prompt injection scenario. Those four are the project.

---

# PART E — QUICK REFERENCE

```bash
# start claude code
claude

# run locally
docker compose up -d          # starts postgres
uvicorn app.main:app --reload # starts the app at localhost:8000

# database migrations
alembic upgrade head

# tests (26 of them, zero API calls, zero cost)
pytest -v

# save your work
git add -A && git commit -m "M5: transaction guard"

# undo everything since last commit
git reset --hard HEAD

# deploy (render auto-deploys on push)
git push
```

**Test payment details** (test mode only):
- UPI success: `success@razorpay`
- UPI failure: `failure@razorpay`
- Card: any test card, then click **Success** or **Failure** on the mock bank page
- Webhook setup OTP: `754081`

**The numbers you will see over and over** (BUILD_SPEC §16.3):
- Correct cart: **516000 paise** = ₹5,160.00
- Poisoned cart: **756000 paise** = ₹7,560.00
- Demo mandate cap: **600000 paise** = ₹6,000.00
- The block: MG-005, 756000 > 600000

---

# THE THREE RULES

1. **One milestone at a time. Commit after each.**
2. **Deploy on day one, not day three.**
3. **Don't build the agent until a real payment has worked.**

Everything else is recoverable.
