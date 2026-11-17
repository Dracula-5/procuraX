# Phase 6: sessions, CSRF, two-factor sign-in, SSO, shared rate limiting, threat model

Branch: `gaps/phase-6-security` (stacked on `gaps/phase-5-contracts-fx-erp`)

## What changed

### Sessions and CSRF (ADR-019)
- Access tokens: 15 minutes (was 60), carry the session id (`sid`); `get_principal` refuses a token
  whose session was revoked. The SPA keeps the access token in memory only (was `sessionStorage`).
- Refresh tokens: 256-bit, HttpOnly cookie (`SameSite=Strict`, `Path=/api/v1/auth`, Secure in production),
  only SHA-256 stored in `auth_sessions` (migration 0020, tenant RLS, no DELETE for the runtime role).
  Rotation on every `POST /auth/refresh`; a replaced token presented again after 30 s revokes the whole
  family (inside 30 s: "superseded", the SPA retries once — two tabs refreshing together). Absolute
  lifetime 14 days, idle timeout 72 h.
- CSRF: `/auth/refresh` and `/auth/logout` need `X-CSRF-Token` (HMAC of the session id, returned in the
  sign-in JSON) and, from browsers, an allowed `Origin`. CORS now allows credentials for the explicit
  origins and the `X-CSRF-Token` header.
- `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `POST /auth/sessions/revoke-others`; deactivating a
  user revokes all their sessions.
- Every sign-in path (password, MFA, SSO, registration, invitation, demo persona) issues a session and
  records its method.
- SPA: single-flight refresh, one retry on 401, session resumed after reload from the cookie, SSO hand-over
  through the URL fragment; sign-out revokes the session on the server.
- Topology: Netlify now proxies `/api/*` to Render (`VITE_API_BASE_URL=/api/v1`, CSP `connect-src 'self'`),
  so the cookie is first-party everywhere (dev Vite proxy and the nginx stack already were).

### Two-factor sign-in (TOTP, ADR-020)
- `app/domain/totp.py`: RFC 6238 without a dependency; recovery codes (10 × 50 bits, peppered HMAC,
  single use).
- Migration 0021: encrypted secret (Fernet, data key), pending secret, enabled time, last accepted step,
  recovery hashes on `users`.
- `/auth/mfa/setup|enable|disable|recovery-codes`, `POST /auth/mfa/verify` (second step after a 5-minute
  challenge token), `POST /users/{id}/mfa/reset` (`user:manage`, not for oneself, signs the user out).
  Production refuses enrolment under the development data key. All changes audited.
- SPA: code step on the sign-in page (app code or recovery code), **Security settings** page (QR code via
  `qrcode-generator` 2.0.4, MIT, no dependencies; manual key; recovery codes shown once with download;
  turn off; new codes; session list with sign-out), "Reset 2FA" in user administration.

### Single sign-on (OpenID Connect, ADR-020)
- `app/integrations/oidc.py`: discovery (cached), authorization code + PKCE (S256), ID-token verification
  against JWKS (signature, iss, aud, exp, nonce; key rotation re-fetch), verified e-mail and optional
  domain allow-list. Login state in an encrypted 10-minute HttpOnly cookie.
- `GET /auth/oidc/start`, `GET /auth/oidc/callback`, `GET /auth/config`; only existing active users;
  failures redirect to `/login?sso_error=<code>` (translated). Behind `PROCURAX_OIDC_ENABLED` (off), with a
  production guard for missing settings.

### Shared rate limiting (ADR-021)
- `auth_throttle` (migration 0022; hashed keys, no tenant data) and `PostgresThrottle`: per address +
  account (5), per address (20), per account (20) per 15 minutes, for password, MFA, MFA management,
  invitation and SSO attempts. Replaces the in-process throttle.

### Docs
- New `docs/security.md` (assets, controls with test status, STRIDE, residual risks, topology and cookie,
  proxy-header caveat); ADR-019/020/021; system design security table; deployment (SSO set-up, MFA key,
  Netlify proxy, troubleshooting); README; interview guide; `.env.example`; roadmap.
- Japanese wording for every new reason code, including Phase 5's (contracts, FX, ledger) that were
  missing.

## How it was tested
- Unit (13): TOTP against all six RFC 6238 SHA-1 vectors (8 and 6 digits), drift and replay, secret and
  otpauth URI, recovery codes (pepper, normalisation, single use), access/MFA token types not
  interchangeable, PKCE against the RFC 7636 example, sealed login state (tampering, expiry, wrong
  state), verified-e-mail and domain rules.
- Integration (28): cookie attributes; refresh rotates; CSRF missing → 401, foreign Origin → 403;
  superseded vs reuse (family revoked, access token dead); logout; sessions list / revoke others /
  deactivation; RLS and no DELETE on `auth_sessions`; two `PostgresThrottle` instances share counts;
  TOTP enrolment, two-step sign-in, replayed code refused, recovery code once, disable; throttled wrong
  codes; admin reset (permissions, not self, signs out); OIDC against `tests/oidc_mock.py` (real HTTP,
  RS256): success and hand-over via refresh, unknown user, unverified e-mail, wrong nonce / audience /
  issuer, state from another browser, feature off. Existing throttle tests ported to the shared store.
- Vitest (10): in-memory token + one renewal on 401, shared refresh with CSRF header and credentials,
  local sign-out when renewal fails, resume after reload, SSO fragment hand-over, MFA step on sign-in,
  SSO button and refusal message, Security page set-up (QR, key, recovery codes) and session list.

## Still open
- **SSO not tested against a real identity provider** (needs your Entra ID / Google / Okta tenant and
  client credentials).
- **The Netlify `/api` proxy is not verified on the live deployment**; check sign-in and a reload after
  deploying (rollback: restore `VITE_API_BASE_URL` to the Render URL and set
  `PROCURAX_REFRESH_COOKIE_SAMESITE=none`).
- MFA is per user, not enforced per organisation or role; no WebAuthn; no step-up for payment approval.
- Client addresses behind the platform proxies can be forged (`--forwarded-allow-ips '*'`); the per-account
  limit still applies. No external penetration test.
