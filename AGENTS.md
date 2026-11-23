# AGENTS.md: shared guide for Claude and Antigravity

Two AI agents (Claude and Antigravity) build this repo together, working in the same folder
(`C:\PromptWars`). **Read this whole file before every task, and update the Task board and Work log
when you finish one.** If anything here conflicts with a request from Ashish (the owner), follow Ashish.

---

## 1. What we're building
**LabLink**, a GenAI-powered campus lab resource-sharing platform for the *PromptWars × Cynergy Coding Club*
hackathon. Problem statement: discover equipment across departments, book slots intelligently, make
project-based access requests, get faculty approval, optimise utilisation, and **resolve scheduling
conflicts fairly** based on deadlines, availability and requirements.

## 2. Hackathon rules (these decide the score)
- An AI auto-scores **Code quality, Security, Efficiency, Testing, Accessibility, Problem alignment**.
- **2 scored submissions only, and the LATEST score counts** (not the best). Never submit a weaker version.
- The GitHub repo must be **public, exactly one branch (`main`), under 10 MB**, linked with a `.git` URL.
- The live URL must work without sign-in at judging time. (Top-10 rule mentions Cloud Run; we're on Render because GCP billing needs a ₹1,000 prepayment. Ashish accepted that Top-10 eligibility risk.)
- Submit a LinkedIn post explaining the project.

## 3. Architecture (where things live)
| File | Responsibility |
|---|---|
| `lablink/__init__.py` | `create_app()`: config, Firestore or memory storage, Gemini client, seeding, error pages |
| `lablink/routes.py` | Thin HTTP layer: parse input, call the service, render. No business rules here. |
| `lablink/services.py` | **All business rules and permission checks** (`PermissionDenied`, `NotFound`) |
| `lablink/fairness.py` | FairShare scoring + allocation. **Deterministic, pure Python.** |
| `lablink/scheduling.py` | Opening hours (Mon–Sat 09–18), capacity, slot search, utilisation |
| `lablink/ai.py` | Gemini calls (`google-genai`) + keyword/template fallbacks |
| `lablink/validation.py` | Server-side validation of every form field |
| `lablink/security.py` | CSRF, CSP and security headers, rate limiter |
| `lablink/repository.py` / `firestore_repo.py` | Storage interface: in-memory (tests/local) and Firestore (prod) |
| `lablink/seed.py` | Demo campus; dates are relative to "now" so the 3D-printer conflict is always live |
| `lablink/templates/`, `static/` | Server-rendered Jinja + one CSS file. **No JavaScript.** |
| `tests/` | pytest suite (87 tests, 95% coverage); `fake_firestore.py` = in-memory Firestore stand-in for tests |

## 4. Non-negotiable rules (break one and the score drops)
1. **Gemini never decides allocations.** `fairness.py` decides; AI only explains, searches and summarises.
2. **Every AI feature needs a fallback** that works with no API key (see `ai.py`). The app must never crash on an AI error.
3. **Treat AI output as untrusted:** check returned IDs against the catalogue, clamp numbers, cap lengths.
   Wrap user text in `<user_input>` and pass it through `_clean()`.
4. **Every POST form includes `{{ csrf() }}`** (macro in `_macros.html`).
5. **No inline `<script>`, `style=""` or `onclick`.** The CSP blocks them. Put styles in `static/style.css`.
6. **Accessibility:** every input has a `<label>`; status is never shown by colour alone; one `<h1>` per page;
   keep keyboard focus visible; tables have captions and `scope`.
7. **Permissions are checked in `services.py`**, never only in templates.
8. **Validate all input in `validation.py`** (length caps, ID allow-list `ID_PATTERN`).
9. **No secrets in the repo.** `serviceAccountKey.json`, `.env` and keys are git-ignored. Cloud Run uses Secret Manager and its service account.
10. **Lean dependencies:** don't add a package without a strong reason. Runtime deps are Flask, gunicorn, google-genai and google-cloud-firestore.
11. Python **3.12+**. Keep the code typed, small functions, docstrings on modules.

## 5. Definition of done (for every change)
```bash
pip install -r requirements-dev.txt
ruff check lablink tests          # must pass
pytest --cov=lablink              # must pass; add tests for new behaviour
flask --app lablink run --debug   # click through the page you changed
```
Update the README if behaviour or setup changes. Repo size must stay well under 10 MB.

## 6. How we avoid stepping on each other
- **Lanes:** each agent owns the files in its lane (below). To edit a file in the other lane, add a
  note in the Task board first and keep the change small.
- **Claim before you start:** move the task to *In progress* with your name. One owner per task.
- **Small, finished steps:** leave the repo passing tests at the end of every task.
- **Git:** single branch `main`. Commit messages start with `[claude]` or `[antigravity]`.
  Never create extra branches, because the rules require exactly one.
- **Handoff:** when you finish, add a dated line to the Work log: what changed, which files, anything the other agent must know.

| Lane | Owner | Files |
|---|---|---|
| Backend logic, fairness, AI layer, tests, security | **Claude** | `lablink/*.py`, `tests/`, `README.md` |
| Deploy & cloud, GitHub, UI polish, live Gemini check, submission material | **Antigravity** | `Dockerfile`, `.github/`, `.gcloudignore`, `templates/`, `static/`, deploy scripts, LinkedIn post |

*Why this split:* Antigravity runs on Ashish's Windows machine, so it can run `gcloud`, `git push`, and
write `.github/` (Claude's remote access can't write there). Claude has the full context of the
backend design and the test suite.

## 7. Task board
### In progress
- #3 Deploy to **Render free tier** · **Ashish** (Render dashboard, needs his GitHub + Gemini key) · steps in README → "Deploy (live demo): Render free tier"

### To do
| # | Task | Lane | Notes |
|---|---|---|---|
| 4 | Verify live Gemini with a real key: search, suggest equipment, faculty brief, allocation explanations, checklist | Antigravity | If the model name errors, set `GEMINI_MODEL`; report back in the Work log |
| 6 | Review the live site against all 6 scoring criteria; fix backend findings | Claude | After task 3 |
| 7 | Firestore smoke test against the real database (book → conflict → waitlist → allocate → apply) | Claude | After task 3 |
| 8 | Draft LinkedIn post + platform project description | Antigravity | Explain the problem, FairShare, Gemini's role |

### Done
- #15 axe audit clean: 0 violations (WCAG 2.1 AA + best practice) on 9 page/role combos plus suggestions, booking clash and 404 on desktop + mobile. Badge colours one shade lighter (same hues), demo bar is an `<aside>` landmark, unique region label on equipment page (Claude, 2026-11-23)
- #14 AI suggestions v2: tiers Needed (quoted evidence) / Worth considering (domain inference, unticked); `project_kind=software_only` → nothing; minimal thinking + 8 s timeout + retry without thinking; honest source labels (Gemini / keyword / "Gemini didn't respond"); default model `gemini-3.5-flash-lite`; `scripts/eval_ai.py` checks 8 real cases against live Gemini. 119 tests (Claude, 2026-11-23)
- #14 Concurrent student booking guardrail: Students cannot book overlapping slots across different equipment. Returns schedule clash error with station name, timing, suppressed waitlist option, and conflict-free slot recommendations. 111 tests pass (Antigravity, 2026-11-23)
- #13 AI relevance fix: Gemini must quote evidence from the student's text + mark necessity; code drops ungrounded/generic/not_needed items; pure software → "No lab equipment needed". Keyword fallback no longer matches 1–2 letter tokens, generic words, department names or descriptions. 109 tests (Claude, 2026-11-23)
- #11 Visual redesign in Ashish's reference style (butter background, ink outlines, hard shadows, pastel department cards, yellow pill buttons). Fonts: Bricolage Grotesque + Outfit, self-hosted in `static/fonts` (OFL). Demo role switcher moved to a top bar. 90 tests pass (Claude, 2026-11-23)
- #10 Commit + push Claude's Render changes (`render.yaml`, `lablink/__init__.py`, `lablink/routes.py`, `tests/test_routes.py`, `README.md`, `AGENTS.md`) (Antigravity, 2026-11-23)
- #2 Create GitHub repo (public), push `main` only (now `https://github.com/Alchemist-47/LabLink.git`; was Alchemist-2905) (Antigravity, 2026-11-23)
- #1 Create `.github/workflows/tests.yml` for CI (Antigravity, 2026-11-23)
- #9 Storage contract tests: same 21 checks run on memory + Firestore (fake client), incl. 12-thread atomic capacity; Firestore code 0% → 97% covered. Suite now 87 tests, 95% coverage (Claude, 2026-11-23)
- Problem analysis, architecture, all 6 required features, FairShare with swaps (Claude, 2026-11-23)
- 65 tests, ruff clean, Dockerfile, README with deploy steps (Claude, 2026-11-23)
- Reviewed Antigravity's ideation doc. Adopted: project weight, swaps, risk levels + safety checklist,
  Who/Why/Risk brief, human sign-off. Dropped: CGPA (sensitive), karma, QR passes, anomaly detection,
  React (Claude, 2026-11-23)

### Parked ideas (only if time allows, and only with tests)
- Faculty "propose alternate time" button on requests
- Student-accepted swaps (notify the moved team before applying)
- Post-session feedback → equipment health flag

## 8. Work log (newest first, one line per handoff)
- 2026-11-23 · Claude · Final audit: repo rules pass, 7/7 CI green, live site verified with Gemini on all pages, a11y 0 violations (#15, replaces task 5). Open: Cloud Run URL question to organisers, LinkedIn post + description (task 8). Needs commit + push.
- 2026-11-23 · Claude · Hotfix: Gemini timeout 8 s → 15 s. Gemini rejects server deadlines under 10 s (400 INVALID_ARGUMENT), which made every call fall back. Guard test added. 120 tests.
- 2026-11-23 · Claude · #14 done. Root causes found: live site never reached Gemini (model `gemini-flash-latest` 404, then 504 timeouts from default thinking), and #13's rules were over-strict. Appended 2 lines to `style.css` (`.tier-heading`). Needs commit + push; then Ashish runs `python scripts/eval_ai.py` with his key.
- 2026-11-23 · Antigravity · #14 done (services.py, equipment.html, test_services.py, test_routes.py). Enforced practical guardrail blocking concurrent overlapping bookings on different equipment; suggestions filter student clashes; 111 tests pass (96% cov), ruff clean.
- 2026-11-23 · Claude · #13 done (ai.py, services.py, routes.py, seed.py tags, requests.html message, tests). Rule for everyone: **any new Gemini feature that recommends equipment must use `_grounded_schema` + `_accept`** so suggestions stay tied to the student's own words. Needs commit + push.
- 2026-11-23 · Antigravity · Refreshed visual palette in `lablink/static/style.css` and `favicon.svg`: modern sandstone linen canvas (`#f4efe6`), electric coral pill buttons (`#ff6338`), obsidian ink (`#141824`), and field-specific department pastels. 90 tests pass, WCAG AA/AAA verified.
- 2026-11-23 · Claude · #11 redesign done (templates + static only, no Python changes). Live at https://lablink-oiz3.onrender.com after push. Task 5 (a11y audit) can start once deployed. Keep the design tokens in `style.css` `:root` when editing.
- 2026-11-23 · Claude · **Repo moved to the submission account: https://github.com/Alchemist-47/LabLink.git** (single branch `main` @ 2d5f867, 46 files, CI workflow included, no secrets). Ashish pushed from Windows after re-auth with `workflow` scope. Old repo Alchemist-2905/LabLink to be deleted/made private. Render must be connected with the Alchemist-47 GitHub account.
- 2026-11-23 · Antigravity · Task 10 done. Ran ruff & pytest (90 passed, 96% cov), committed and pushed Render blueprint & healthz updates to main. Ready for Ashish to deploy on Render (task 3).
- 2026-11-23 · Claude · Render support ready: `render.yaml` blueprint (free, Singapore, /healthz), secure cookies + HSTS auto-enabled on Render (`RENDER` env), AI rate limit now per-browser + global cap. 90 tests, 96% coverage, ruff clean. **Antigravity: task 10 (commit + push), then Ashish deploys on Render.** Cloud Run steps kept in README as an alternative.
- 2026-11-23 · Claude · Hosting changed: no Google Cloud billing, so we deploy to **Render free tier** (Docker, from GitHub). Storage = in-memory demo data (Firestore needs GCP billing setup we're skipping). Claude is preparing the code; Antigravity please pause task 3.
- 2026-11-23 · Antigravity · Task 2 done. Created public repo https://github.com/Alchemist-2905/LabLink.git, pushed single branch main (45 files, 0.63 MB, zero secrets). Starting Task 3 (Cloud Run deploy).
- 2026-11-23 · Antigravity · Task 1 done. Created `.github/workflows/tests.yml` with Ubuntu 3.12 CI test run. Starting Task 2 (GitHub repo).
- 2026-11-23 · Claude · #9 done. `FirestoreRepository` now takes an optional `client=` (tests inject a fake; prod unchanged). New: `tests/fake_firestore.py`, `tests/test_repositories.py`; README test section updated. Antigravity: no action needed, just include these in your next commit.
- 2026-11-23 · Claude · Initial build of LabLink in `C:\PromptWars`; created this file. `.github/workflows/tests.yml` still needs to be created (task 1).

## 9. Reference
**Key environment variables:** `SECRET_KEY`, `LABLINK_STORAGE` (`memory`|`firestore`), `GOOGLE_CLOUD_PROJECT`,
`GEMINI_API_KEY` (or `GEMINI_USE_VERTEX=1`), `GEMINI_MODEL` (default `gemini-flash-latest`), `AI_RATE_LIMIT`.
Full table in README.

**Demo:** role switcher in the header (no sign-in). Lab Coordinator → *Fair allocation* shows 4 teams competing for
the 3D printer, with allocated / equivalent machine / swap / later-slot outcomes.

**CI workflow** (`.github/workflows/tests.yml`):
```yaml
name: tests
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements-dev.txt
      - run: ruff check lablink tests
      - run: pytest --cov=lablink --cov-report=term-missing
```
