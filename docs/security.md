# Security model and threat model

How ProcuraX protects sign-in, sessions and tenant data, what each control defends against, and what is
still open. Controls marked *tested* have automated tests in `backend/tests` or `frontend/src`; nothing
here has had an external penetration test.

## Assets and trust boundaries

| Asset | Why it matters |
|---|---|
| Tenant business data (requests, vendors, bank accounts, invoices, contracts, budgets) | Confidential to one company; bank details enable payment fraud |
| Approval decisions and the audit trail | Segregation of duties and evidence for auditors |
| Sign-in sessions, passwords, TOTP secrets, recovery codes | Whoever holds them acts as the user |
| Payment files (Zengin) and journal exports | Move money / post to the ledger once a person uploads them |

Boundaries: the browser (untrusted), the API (trusted code, runs as `procurax_app`), PostgreSQL (RLS
enforced for the runtime role), object storage, and outside services (SMTP, Slack/Teams, the NTA Web-API,
the ECB, an OpenID Connect provider), each behind a `PROCURAX_*` flag that is off by default.

## Sign-in and sessions (ADR-019)

```
password ─▶ POST /auth/login ──(MFA on)──▶ challenge (5 min) ─▶ POST /auth/mfa/verify ─┐
SSO ─▶ /auth/oidc/start ─▶ provider ─▶ /auth/oidc/callback (state, PKCE, ID token) ─────┤
                                                                                       ▼
                 access token (JWT, 15 min, in memory) + refresh token (HttpOnly cookie)
                                                                                       │
             POST /auth/refresh (cookie + X-CSRF-Token + allowed Origin) ─ rotates ◀────┘
```

| Control | Defends against | Where | Tested |
|---|---|---|---|
| Argon2id password hashes; same error and a dummy-hash check for unknown e-mails | Offline cracking; account enumeration | `core/security.py` | ✅ |
| Access token only in memory, 15 minutes, carries the session id (`sid`) | Token theft from storage; long-lived stolen tokens. Revoking the session ends its access tokens at the next request | `lib/api.ts`, `api/deps.py` | ✅ |
| Refresh token: 256-bit random, HttpOnly, `SameSite=Strict`, `Path=/api/v1/auth`, Secure in production; only its SHA-256 stored | Script access to the session (XSS cannot read it); database leak replay | `identity/sessions.py`, migration 0020 | ✅ |
| Rotation on every refresh; a replaced token used again after 30 s revokes the whole session family | Stolen refresh tokens (the thief and the user collide, both are signed out) | `sessions.rotate` | ✅ |
| Absolute lifetime (14 days) and idle timeout (72 h) | Forgotten sessions | settings | ✅ |
| CSRF: cookie endpoints need `X-CSRF-Token` (HMAC of the session id) and, from browsers, an allowed `Origin` | Another site making the browser refresh or sign out | `identity/router.py` | ✅ |
| Sign-out revokes the family and clears the cookie; "sign out everywhere else"; deactivation and MFA reset revoke all sessions | Lost devices, leavers | `/auth/logout`, `/auth/sessions` | ✅ |
| Per-user session list (browser, method, last use) | Users not noticing a foreign session | Security page | ✅ |

The CSRF token is not secret from scripts on ProcuraX's own origin (it sits in `localStorage` so a new tab
can resume the session); its purpose is to be unknown to *other* sites. Cross-site scripting would still
let an attacker act inside the open page; the defences against that are React's escaping, a strict
Content-Security-Policy (`default-src 'self'`, no inline scripts) and no HTML rendering of user input.

### Deployment topology and the cookie

The refresh cookie is first-party only when the SPA and the API share a site. That holds in development
(Vite proxy), in the Docker stack (nginx proxies `/api`) and on Netlify, which now proxies `/api/*` to
Render (`netlify.toml`). If the API is ever called cross-site again, set
`PROCURAX_REFRESH_COOKIE_SAMESITE=none`; browsers that block third-party cookies (Safari) will then not
keep the session across reloads, so a proxy is the recommended setup.

## Two-factor sign-in (TOTP)

| Control | Defends against | Tested |
|---|---|---|
| RFC 6238 (HMAC-SHA1, 30 s, 6 digits), ±1 step drift; verified against the RFC test vectors | Password-only compromise | ✅ |
| Last accepted step stored under a row lock: a code works once | Replay of an observed code | ✅ |
| Secret encrypted with `PROCURAX_DATA_ENCRYPTION_KEY` (Fernet, rotation via comma-separated keys); production refuses enrolment under the development key | Database leak revealing secrets | ✅ |
| 10 recovery codes (50 bits each), shown once, stored as HMAC-SHA256 with a server pepper, single use | Lost phone without help-desk; offline guessing of a leaked table | ✅ |
| The password step returns a 5-minute challenge token, not an access token | Half-finished sign-ins being used as sessions | ✅ |
| Wrong codes count against the shared throttle; an administrator (`user:manage`) can reset a colleague's MFA (audited, signs them out), not their own | Code guessing; lockout without recovery | ✅ |

Not built: enforcing MFA per organisation or per role, WebAuthn/passkeys, step-up for sensitive actions
(e.g. payment approval).

## Single sign-on (OpenID Connect)

Authorization code flow with PKCE (S256) against one provider configured by `PROCURAX_OIDC_*`. The state,
nonce and PKCE verifier travel in an encrypted, 10-minute, HttpOnly cookie (`SameSite=Lax`, scoped to
`/api/v1/auth/oidc`). The ID token is verified against the provider's JWKS (signature, `iss`, `aud`, `exp`,
`nonce`; RSA/ECDSA algorithms only). Only an **existing, active** user whose `email` claim matches and has
`email_verified: true` can sign in (optionally limited to `PROCURAX_OIDC_ALLOWED_DOMAINS`); SSO never
creates accounts. After the callback the API sets the refresh cookie and redirects to `/app#sso=<csrf>`;
the fragment never reaches a server.

Tested end to end against a local provider stand-in (`tests/oidc_mock.py`, real HTTP, RS256): success,
unknown user, unverified e-mail, wrong nonce, wrong audience, wrong issuer, state from another browser,
feature off. **Not tested against a real identity provider** (needs your Entra ID / Google / Okta tenant).
Not built: SAML, SCIM provisioning, "SSO only" enforcement per organisation, provider-side logout.

## Brute-force protection (ADR-021)

Failed sign-in, MFA, MFA-management, invitation and SSO attempts are counted in PostgreSQL
(`auth_throttle`), so every API process and replica sees the same numbers. Limits per window
(`PROCURAX_LOGIN_LOCKOUT_SECONDS`, default 15 minutes): 5 per address + account, 20 per address, 20 per
account (distributed guessing). Keys are SHA-256 hashes, so neither e-mail nor IP addresses are stored in
clear. Tested with two throttle instances sharing the table.

The per-address part depends on the client address the API sees. Uvicorn runs with
`--proxy-headers --forwarded-allow-ips '*'` (Render and Netlify sit in front), so a client could forge
`X-Forwarded-For`; the per-account limit still applies in that case. Narrow `--forwarded-allow-ips` to the
platform's proxy range where the platform publishes one.

## Tenant isolation and data access

Unchanged and covered by tests for every tenant table: application filters on `org_id`, plus PostgreSQL
row-level security (`ENABLE` + `FORCE`, predicate from migration 0001) for the runtime role, which is
neither owner nor superuser. Explicit bypass (`system_session`) only where no tenant is known yet: sign-in,
MFA verification, refresh, invitation acceptance, SSO, worker sweeps. `auth_sessions` is a tenant table;
`auth_throttle` and the NTA registry cache hold no tenant data.

## STRIDE summary

| Threat | Example | Main controls | Residual risk |
|---|---|---|---|
| Spoofing | Password guessing; stolen password | Argon2id, shared throttle, TOTP, SSO | MFA is opt-in per user |
| Tampering | Changing another tenant's records; swapping an invoice file | RLS + app scoping; immutable file records (trigger) | — |
| Repudiation | "I never approved that" | Append-only audit log (DB grants), actor and session method recorded | Audit rows are not signed or chained |
| Information disclosure | Cross-tenant reads; token theft via XSS | RLS; in-memory access token; HttpOnly refresh cookie; CSP | XSS inside the page can still act as the user |
| Denial of service | Locking a victim out by failing their logins | Per-account limit is 4× the main limit and expires with the window | No CAPTCHA or proof-of-work |
| Elevation of privilege | Employee approving payments; admin approving spend | Server-side permission checks, segregation of duties in RBAC, AI cannot decide (ADR-012 / brief) | Role changes take effect at the next request, not retroactively |

## Secrets

All secrets come from environment variables (`PROCURAX_JWT_SECRET`, `PROCURAX_DATA_ENCRYPTION_KEY`,
`PROCURAX_SMTP_PASSWORD`, `PROCURAX_S3_SECRET_ACCESS_KEY`, `PROCURAX_OIDC_CLIENT_SECRET`, …); production
refuses to start with the development JWT secret and lists every missing setting at once. Nothing secret is
committed; the test stand-ins (OIDC client secret, NTA application ID) are fixed strings used only against
local mocks.

## Reporting a vulnerability

This is a portfolio project without a security team. Please open a private GitHub security advisory on
the repository rather than a public issue.
