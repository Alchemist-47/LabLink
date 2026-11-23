# LabLink: Making College Laboratories More Accessible

LabLink is a GenAI-powered platform for sharing lab resources across a campus. Students can find specialised equipment, development kits and workstations in **any** department, request access for a project, and book slots. Faculty approve requests with the help of an AI brief. When too many teams want the same machine, LabLink resolves the clash with a **transparent, deadline-aware fairness algorithm** and uses Gemini to explain each outcome in plain language.

## How it covers the challenge

| Challenge requirement | What LabLink does |
|---|---|
| Equipment discovery across departments | Search in plain words, e.g. *"measure a 50 MHz signal from my FPGA"* → Gemini matches it against the whole campus catalogue and explains each match. Filters by department and type. Live "free this hour" status. |
| Intelligent slot booking | Week view of availability per machine. Capacity is checked atomically (a Firestore transaction), so the last unit can never be double-booked. If a slot is full, LabLink offers the next free slots **before your deadline**, equivalent machines in other departments, or a place on the waitlist. |
| Project-based access requests | Describe the project once and Gemini proposes the equipment and hours it needs. One request goes to the faculty guide and covers every machine. |
| Faculty approvals | Each pending request has an AI **Who / Why / Risk** brief. Only the project's guide (or the lab coordinator) can approve or reject it. |
| Optimising resource utilisation | Utilisation dashboard (last 14 days, upcoming hours, waitlists) with AI recommendations, e.g. promote idle machines or add capacity where waitlists form. |
| Fair and feasible conflict resolution | **FairShare** engine (below). It recommends a slot on the requested machine, an equivalent machine elsewhere, or a swap with a lower-priority team. Nothing changes until faculty apply the plan. |
| Safety | Every machine has a risk level and training flag. A **safety prep checklist** is generated for the student's planned use. |

## FairShare: how conflicts are resolved

Waitlisted bookings are ranked by a priority score out of 100:

| Part | Points | Why |
|---|---|---|
| Urgency | 0–40 | The closer the project deadline, the higher the score |
| Project type | 0–20 | Capstone/research > club > routine coursework |
| Fairness | 0–25 | Fewer lab hours used in the last 30 days scores higher, so nobody hogs a machine |
| Waiting | 0–15 | Teams that have been waiting longer score higher |

Teams are placed highest score first, in the first slot before their deadline. LabLink tries, in order:

1. the requested machine (the originally requested time, if it is free);
2. an **equivalent machine** in another department (same category);
3. a **swap**: a team holding a slot before the deadline is asked to move to another slot that still meets *their* deadline. This only happens if that team scores at least 10 points lower and has a later deadline;
4. otherwise, the earliest slot after the deadline, flagged for the guide.

The algorithm is plain, deterministic Python (`lablink/fairness.py`), so the same inputs always give the same outcome and the whole thing is unit-tested. **Gemini does not make allocation decisions.** It only explains them.

The seeded demo has a live conflict: four teams want the Mechanical department's 3D printer, which a coursework team booked early for several days. Switch to *Lab Coordinator* → **Fair allocation** to see the plan, which covers all four outcome types, then apply it.

## Architecture

```
Browser (server-rendered HTML, no JavaScript)
   │
Flask app (Cloud Run, gunicorn)
   ├── routes.py        thin HTTP layer, role checks, rate limiting
   ├── services.py      business rules and permissions
   ├── fairness.py      FairShare scoring + allocation (deterministic)
   ├── scheduling.py    opening hours, capacity, slot search
   ├── ai.py            Gemini (google-genai) with keyword/template fallbacks
   ├── validation.py    server-side validation of every input
   ├── security.py      CSRF, CSP and security headers, rate limiter
   └── repository.py    MemoryRepository (tests/local)  |  firestore_repo.py (production)
```

**Design choices**
- **Gemini is optional at runtime.** If there's no key, a timeout or a quota error, every feature falls back to keyword matching or templates, so the app never breaks. Responses are cached (LRU) and AI endpoints are rate-limited.
- **Prompt-injection safe.** Student text is passed inside delimiters, labelled as untrusted data, and stripped of tag characters. Every equipment ID Gemini returns is checked against the real catalogue before use. Hours are clamped.
- **Lean.** Four runtime dependencies, no front-end build, a single container.

## Security
- No secrets in the repository. `SECRET_KEY` and `GEMINI_API_KEY` come from Secret Manager on Cloud Run, and Firestore uses the service account (Application Default Credentials).
- CSRF tokens on every form, strict Content-Security-Policy (`default-src 'self'`, no inline scripts or styles), `X-Frame-Options: DENY`, `nosniff`, HSTS, secure/HttpOnly/SameSite cookies.
- All permissions are enforced server-side: students book only with their own approved requests; only the guide or coordinator decides requests; only faculty or the coordinator allocate. The allocation plan is recomputed on the server when applied, never taken from the client.
- 32 KB request-size limit, length caps and ID allow-listing on all inputs, open-redirect protection, non-root container user.

## Accessibility
- Semantic landmarks, a skip link, one `h1` per page, `aria-current` navigation.
- Every input has a visible label; errors use `role="alert"`, confirmations use `role="status"`.
- Fully keyboard-operable (no JavaScript), clearly visible focus outlines, WCAG AA colour contrast. Status is never conveyed by colour alone (the availability grid shows numbers or "Full").
- Tables use captions and scoped headers; respects `prefers-reduced-motion`; responsive down to mobile.

## Roles (demo)
There's no sign-in for the demo. Use the **Viewing as** switcher in the header:
- **Students:** Ananya, Rahul, Meera, Kiran, Arjun, Neha
- **Faculty:** Prof. Rao, Prof. Iyer, Dr. Khan
- **Admin:** Lab Coordinator

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
copy .env.example .env          # optional: add GEMINI_API_KEY
flask --app lablink run --debug
```

Without any settings it runs with in-memory demo data and the keyword fallback. To run locally against real Firestore, set `LABLINK_STORAGE=firestore`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS=serviceAccountKey.json` in `.env`. The key file is git-ignored and excluded from deploy uploads. **Never commit it.**

## Tests

```bash
pytest --cov=lablink          # 87 tests, 95% coverage
ruff check lablink tests
```

They cover scheduling, FairShare fairness and swaps, AI fallbacks and prompt-injection handling, permissions, routes, CSRF and headers. **Storage contract tests** run the same checks against both the in-memory store and the Firestore store (using an in-memory fake Firestore client, so no credentials are needed), including atomic capacity under 12 concurrent bookings.

The tests also run on every push via GitHub Actions (`.github/workflows/tests.yml`).

## Deploy to Google Cloud Run

```bash
PROJECT=your-project-id
REGION=asia-south1
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com firestore.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com

# Firestore (Native mode), once per project
gcloud firestore databases create --location=$REGION

# Secrets
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets create lablink-secret-key --data-file=-
printf "%s" "YOUR_GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-

# Let the Cloud Run service account read secrets and use Firestore
SA=$(gcloud projects describe $PROJECT --format='value(projectNumber)')-compute@developer.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/datastore.user
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor

gcloud run deploy lablink --source . --region $REGION --allow-unauthenticated \
  --set-env-vars LABLINK_STORAGE=firestore,GOOGLE_CLOUD_PROJECT=$PROJECT \
  --set-secrets SECRET_KEY=lablink-secret-key:latest,GEMINI_API_KEY=gemini-api-key:latest \
  --memory 512Mi --max-instances 3
```

On first start, an empty Firestore database is seeded with the demo campus. To use Vertex AI instead of an API key, set `GEMINI_USE_VERTEX=1` and grant the service account `roles/aiplatform.user`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | random (warns) | Signs session cookies. Required in production. |
| `LABLINK_STORAGE` | `memory` | `firestore` on Cloud Run |
| `GOOGLE_CLOUD_PROJECT` | – | Firestore / Vertex project |
| `FIRESTORE_DATABASE` | `(default)` | Named Firestore database |
| `GEMINI_API_KEY` | – | Gemini API key (AI Studio) |
| `GEMINI_USE_VERTEX` | – | `1` to use Vertex AI with the service account |
| `GEMINI_MODEL` | `gemini-flash-latest` | Model name |
| `AI_RATE_LIMIT` | `20` | AI requests per client per minute |
| `CAMPUS_TZ` | `Asia/Kolkata` | Campus time zone |
