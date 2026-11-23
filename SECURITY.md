# Security

## How LabLink protects users and data

| Threat | Protection | Where |
|---|---|---|
| Cross-site request forgery | Every form POST carries a per-session CSRF token, checked in constant time (`hmac.compare_digest`) | [`lablink/security.py`](lablink/security.py) |
| Cross-site scripting | Jinja auto-escaping on every page; strict Content-Security-Policy (`default-src 'self'`, no inline scripts, `object-src 'none'`); the app ships no JavaScript | [`lablink/security.py`](lablink/security.py) |
| Clickjacking, sniffing, leaks | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy`; HSTS behind HTTPS | [`lablink/security.py`](lablink/security.py) |
| Session theft | Signed session cookies, `HttpOnly`, `SameSite=Lax`, `Secure` when served over HTTPS | [`lablink/__init__.py`](lablink/__init__.py) |
| Bad or hostile input | Every form field is validated on the server (types, ranges, lengths, id pattern, NUL bytes stripped); request bodies capped at 32 KB | [`lablink/validation.py`](lablink/validation.py) |
| Acting as someone else | Role and ownership checks in the service layer for every action (students only book with their own projects; only a project's guide decides it; only staff allocate) | [`lablink/services.py`](lablink/services.py), [`lablink/policy.py`](lablink/policy.py) |
| Abuse and cost | Rate limits per browser and globally: AI calls 20/min (120 global), form submissions 30/min (300 global) | [`lablink/security.py`](lablink/security.py) |
| Prompt injection | Student text is passed to Gemini inside `<user_input>` tags as untrusted data; responses must match a JSON schema; every equipment id is checked against the real catalogue; "needed" suggestions must quote the student's own words | [`lablink/ai.py`](lablink/ai.py) |
| Overbooking races | Capacity is checked and the booking saved atomically (a lock in memory, a transaction in Firestore) | [`lablink/repository.py`](lablink/repository.py), [`lablink/firestore_repo.py`](lablink/firestore_repo.py) |
| Leaking internals | Error pages never show stack traces or exception text | [`lablink/__init__.py`](lablink/__init__.py) |
| Secrets | The Gemini key comes only from the environment, never from the repo or the browser; `.env` and credential files are git-ignored | [`.gitignore`](.gitignore), [`.env.example`](.env.example) |
| Vulnerable dependencies | `pip-audit` and the `ruff` security rules (bandit set) run in CI on every push; four runtime dependencies, all pinned | [`.github/workflows/tests.yml`](.github/workflows/tests.yml) |
| Container escape | The Docker image runs as an unprivileged user | [`Dockerfile`](Dockerfile) |

Security behaviour is covered by tests in [`tests/test_security.py`](tests/test_security.py) (CSRF, headers,
rate limits, identity handling) and [`tests/test_routes.py`](tests/test_routes.py) (role and ownership checks).

## Demo mode

The public demo runs with `DEMO_MODE=1`: there is no sign-in, and anyone can switch between demo users to see
every role. This is deliberate, so judges can try the whole flow. With `DEMO_MODE=0`, LabLink trusts only the
identity set by Google's Identity-Aware Proxy (`X-Goog-Authenticated-User-Email`), matched to a user's email;
role switching and demo reset are disabled.

## Reporting a vulnerability

Please open a private security advisory on the GitHub repository rather than a public issue.
