# Free deployment (no credit card)

Total cost: ¥0. All four services have free tiers that need no payment card:

| Piece | Service (free tier) | What you get | Limits that matter |
|---|---|---|---|
| Database | [Neon](https://neon.tech) Free | PostgreSQL 16, 0.5 GB, permanent | Compute sleeps after 5 idle minutes (sub-second wake); limited connections |
| API | [Render](https://render.com) Free web service (Docker) | FastAPI container with Tesseract OCR | Sleeps after 15 idle minutes; the first request then takes about a minute; 512 MB RAM |
| Web app | [Netlify](https://www.netlify.com) Free | The React build on a CDN, rebuilt on every push | Monthly build-minute and bandwidth caps (ample for a demo) |
| Schedules | GitHub Actions | Migrations on release, hourly SLA sweep, nightly demo reset | Scheduled workflows pause after 60 days without repository activity |

Render's own free PostgreSQL is not used because it expires after 30 days; Neon's free database
does not. Free-tier terms change, so check each provider's pricing page.

Order matters: **database → API → web → GitHub secrets → first release.**

## 1. Repository

The project is on GitHub (`main`). CI runs on every push. Until the secrets in step 5 exist, the
deploy and maintenance workflows log "not configured" and do nothing.

## 2. Neon database

1. neon.tech → **New project** → PostgreSQL **16**. Pick the region nearest your Render service: for
   Render **Oregon (US West)** choose **AWS US West 2 (Oregon)**; for Render Singapore choose AWS Singapore.
   The live demo runs in Oregon (`render.yaml` says `region: oregon`). Render fixes a region when the
   service is created, so moving closer to Japan means creating a new Singapore service and database.
2. **SQL Editor**: create the least-privilege runtime role, with your own strong password.

   ```sql
   CREATE ROLE procurax_app LOGIN PASSWORD 'replace-with-a-long-random-password' NOSUPERUSER NOBYPASSRLS;
   GRANT CONNECT ON DATABASE neondb TO procurax_app;
   ```

3. **Connect** → turn **Connection pooling off** (asyncpg needs the direct host) → copy the connection
   string. Paste it exactly as shown; the backend converts `postgresql://…?sslmode=require&channel_binding=require`
   to the asyncpg form itself. You need two strings:
   - **Owner** (migrations, demo seed/reset): the string for `neondb_owner`.
   - **Runtime** (the API): the same string with user `procurax_app` and the password you chose.
     Percent-encode special characters in the password, for example `@` becomes `%40`.

## 3. Render: the API

**New → Web Service** → connect the GitHub repository → runtime **Docker**, plan **Free**.

| Setting | Value | Why |
|---|---|---|
| Root Directory | `backend` | Render then prefixes `backend/` to the paths below |
| **Dockerfile Path** | `./Dockerfile` (shows as `backend/ ./Dockerfile`) | Typing `backend/Dockerfile` makes Render look for `backend/backend/Dockerfile` |
| Docker Build Context Directory | `.` (shows as `backend/ .`) | |
| Docker Command | *(empty)* | The Dockerfile starts uvicorn on Render's `$PORT` |
| Pre-Deploy Command | *(leave; locked on free)* | GitHub Actions runs migrations instead |
| **Auto-Deploy** | **Off** | Otherwise Render deploys new code before migrations run; the Deploy workflow triggers it after migrating |
| Health Check Path | `/ready` | Checks the database connection before traffic switches |

**Environment** tab:

| Key | Value |
|---|---|
| `PROCURAX_ENV` | `prod` |
| `PROCURAX_DEMO_MODE` | `true` |
| `PROCURAX_PUBLIC_DEMO` | `true` |
| `PROCURAX_JWT_SECRET` | click **Generate** |
| `PROCURAX_DATABASE_URL` | Neon **runtime** string (step 2) |
| `PROCURAX_PUBLIC_APP_URL` | the Netlify URL from step 4, e.g. `https://procurax.netlify.app`. Until then use `https://example.com` and update it later |
| `PROCURAX_SLA_ESCALATION_INTERVAL_SECONDS` | `900` |
| `PROCURAX_OCR_LANGUAGES` | `eng+jpn` |
| `PROCURAX_DB_POOL_SIZE` | `3` |
| `PROCURAX_DB_MAX_OVERFLOW` | `2` |

Production settings refuse to start without the JWT secret, a database URL and an `https://` public URL.
That is deliberate: a misconfigured deploy fails loudly instead of running insecurely.

Copy **Settings → Deploy Hook** for step 5. [`render.yaml`](../render.yaml) describes the same API
service as a Blueprint, an alternative to these dashboard steps.

## 4. Netlify: the web app

**Add new site → Import an existing project → GitHub** → pick the repository. Netlify reads
[`netlify.toml`](../netlify.toml) (base `frontend`, command `npm run build`, publish `dist`, Node 22,
SPA rewrite, security headers), so leave the build fields as detected.

Netlify proxies `/api/*` to the API on Render (the first rule in `netlify.toml`, currently
`https://procurax-mod8.onrender.com`), and the SPA calls `/api/v1` on its own site. One site for the
browser keeps the sign-in cookie first-party (see [security.md](security.md)). If your Render URL differs,
change that rule and push; Netlify rebuilds. Then set the API's `PROCURAX_PUBLIC_APP_URL` (step 3) to the
exact Netlify URL (`https://…netlify.app`, no trailing slash); it is also the only browser origin the API's
CORS policy allows. Netlify's proxy waits a limited time for an answer, so the first request after Render's
free instance has slept can fail once; the page retries.

Netlify rebuilds the site on every push to `main` by itself; no deploy hook is needed.

## 5. GitHub secrets, then the first release

Repository → **Settings → Secrets and variables → Actions** → **New repository secret**:

| Secret | Value |
|---|---|
| `NEON_OWNER_DATABASE_URL` | Neon owner string |
| `NEON_APP_DATABASE_URL` | Neon runtime string |
| `RENDER_API_DEPLOY_HOOK` | Render API deploy hook (optional: without it, migrations still run, and you redeploy from Render) |

Then **Actions → Deploy → Run workflow**. It runs `alembic upgrade head` as the owner (the release
step), creates the fictional demo company if it does not exist yet, and triggers the Render deploy.
Render switches traffic only once `/ready` returns OK. From then on, every push to `main` that passes
CI migrates and redeploys the API, and Netlify rebuilds the web app. The demo tenant resets at 03:00 JST
and the SLA sweep runs hourly, even while the API sleeps.

## 6. Check it

From `backend/`: `uv run python scripts/live_check.py --api <render-url> --web <netlify-url>` runs 13
end-to-end checks against the live deployment (CORS, persona sign-in, a purchase request through
policy and approval, analytics, RBAC, assistant, registration lock) and exits non-zero on any failure.
`/api/v1/meta` shows the deployed commit and the CORS origins in effect.


- `https://<render-api>/ready` returns `{"status":"ready","database":"ok"}`.
- `https://<render-api>/api/v1/meta` returns `"public_demo": true` and `"registration_enabled": false`.
- Open the Netlify URL. The first load after a quiet period shows "Waking up the free-tier API" for up
  to a minute; then the public-demo banner appears and **Create organisation** is hidden.
- Sign in as any persona on the login page; [user_personas.md](user_personas.md) has walkthroughs.

## Notifications (optional channels)

In-app notifications work with no extra setup: the API runs the outbox worker on an interval
(`PROCURAX_NOTIFICATION_WORKER_INTERVAL_SECONDS=30` in `render.yaml`), and while a free instance sleeps
the hourly *Demo maintenance* workflow runs one batch (`python -m app.worker --once`). A dedicated
Render worker is a paid service type; `render.yaml` has a commented block for it.

E-mail and Slack/Teams stay **off** until you choose to provide credentials (they may cost money with
some providers):

| To enable | Set on the Render service |
|---|---|
| E-mail | `PROCURAX_EMAIL_ENABLED=true`, `PROCURAX_SMTP_HOST`, `PROCURAX_SMTP_PORT`, `PROCURAX_SMTP_USERNAME`, `PROCURAX_SMTP_PASSWORD`, `PROCURAX_SMTP_STARTTLS=true`, `PROCURAX_EMAIL_FROM` (a sender your provider has verified) |
| Slack / Teams | `PROCURAX_WEBHOOKS_ENABLED=true` and `PROCURAX_DATA_ENCRYPTION_KEY` (32+ random characters; the blueprint generates one). Each tenant admin then pastes an incoming-webhook URL under *Notifications → Team channels* |

The public demo's personas use `example.com` addresses, which cannot receive mail. Failed deliveries are
retried with backoff and then listed under *Notifications → Failed deliveries* for the org admin.

## Invoice files (object storage)

Production refuses to store invoice files on the container's disk (Render's disk is not durable). To
enable uploads, create a bucket and set on the Render service:

| Setting | Value |
|---|---|
| `PROCURAX_STORAGE_BACKEND` | `s3` |
| `PROCURAX_S3_BUCKET` | your bucket (private; block public access) |
| `PROCURAX_S3_ENDPOINT_URL` | empty for AWS S3; `https://<account>.r2.cloudflarestorage.com` for Cloudflare R2 |
| `PROCURAX_S3_REGION` | e.g. `ap-northeast-1`, or `auto` for R2 |
| `PROCURAX_S3_ACCESS_KEY_ID` / `PROCURAX_S3_SECRET_ACCESS_KEY` | a key limited to `s3:PutObject` and `s3:GetObject` on that bucket |
| `PROCURAX_DOCUMENT_RETENTION_YEARS` | `7` (Japanese corporate tax law; longer if losses are carried forward) |

Each file records its retain-until date; ProcuraX never deletes files. Use bucket versioning, or object
lock in governance/compliance mode, if you need storage-level enforcement. Optional virus scanning needs a
reachable `clamd` (`PROCURAX_CLAMAV_ENABLED`, `PROCURAX_CLAMAV_HOST`); there is no free managed ClamAV,
so on the free tier leave it off and decide whether `PROCURAX_ALLOW_UNSCANNED_DOCUMENTS=true` is
acceptable (without it, invoices with attached files cannot be paid).

## NTA registry lookup (optional)

Apply for a Web-API application ID on the National Tax Agency's qualified-invoice issuer publication site
(free; the NTA reviews the request). Then set `PROCURAX_NTA_REGISTRY_ENABLED=true` and
`PROCURAX_NTA_APPLICATION_ID`. Production refuses to start with the flag on and no ID. Until then,
registration numbers are checked by check digit and vendor master only. See [einvoicing.md](einvoicing.md).

## Exchange rates (optional)

Requests in a currency other than the organisation's base currency need a rate, or they are blocked at
submission (VAL-004). Finance managers or administrators enter rates on **Exchange rates**. To fetch the
ECB's euro reference rates instead (free, no key, published on ECB working days), set
`PROCURAX_FX_ECB_ENABLED=true` and use **Fetch ECB reference rates**; nothing is fetched automatically.
The ECB publishes these rates for information only, so keep manual rates if your accounting policy uses your
bank's rates (a manual rate wins on the same day).

## Single sign-on (optional)

Register ProcuraX as a web application with your identity provider (Microsoft Entra ID, Google Workspace,
Okta, Keycloak, ...): redirect URI `https://<your web host>/api/v1/auth/oidc/callback`, scopes
`openid email profile`. Then set `PROCURAX_OIDC_ENABLED=true`, `PROCURAX_OIDC_ISSUER` (the issuer URL, e.g.
`https://login.microsoftonline.com/<tenant-id>/v2.0`), `PROCURAX_OIDC_CLIENT_ID`,
`PROCURAX_OIDC_CLIENT_SECRET`, `PROCURAX_OIDC_REDIRECT_URI` and optionally `PROCURAX_OIDC_DISPLAY_NAME` and
`PROCURAX_OIDC_ALLOWED_DOMAINS`. Production refuses to start with the flag on and a setting missing. Only
users who already exist in ProcuraX (invited) and whose verified e-mail matches can sign in.

## Two-factor sign-in

Users turn it on under **Security settings**. It needs `PROCURAX_DATA_ENCRYPTION_KEY` (Render generates
one); keep that key stable, or list the old one after the new one when rotating, or existing secrets become
unreadable.

## Journal export

Set the ledger accounts on **Journal export** (organisation admin). Finance managers then export matched
and paid invoices as CSV or JSON for the ERP; each document is exported once and the file stays
downloadable. Nothing is posted to an ERP automatically.

## If something does not work

| Symptom | Likely cause | Fix |
|---|---|---|
| Render: `failed to read dockerfile` / not found | Dockerfile Path includes `backend/` twice | Set it to `./Dockerfile` with Root Directory `backend` |
| Render log: `PROCURAX_JWT_SECRET must be set` or `…must use HTTPS` | Environment variables missing | Complete the step 3 Environment table |
| Render deploy fails on `/ready` | Wrong runtime DB string, or `procurax_app` missing | Re-check step 2; the Render log shows the database error |
| API errors such as `relation "…" does not exist` | Migrations not run | Add the step 5 secrets and run **Actions → Deploy** |
| Web shows "API is not reachable" | The `/api/*` proxy rule in `netlify.toml` points elsewhere, or Render is still waking | Fix the rule and push, or wait a minute and reload |
| Signed out after every reload | The SPA and API are on different sites and the browser blocks the cookie | Keep the Netlify `/api` proxy (recommended), or set `PROCURAX_REFRESH_COOKIE_SAMESITE=none` |
| Browser console shows a CORS error | `PROCURAX_PUBLIC_APP_URL` differs from the Netlify URL | Set it exactly (https, no trailing slash); Render redeploys on save |
| Login page shows no personas | Demo tenant not created | Run **Demo maintenance** with *Reset the demo tenant* ticked |

## How the public demo is protected

- `PROCURAX_PUBLIC_DEMO=true` is the only way production settings accept demo mode. Persona login
  works only for the organisation flagged `is_demo`, and every persona login is audited.
- Self-registration is off in public demo mode, so anonymous visitors cannot store their own data.
- The JWT secret is generated by Render and never stored in the repository.
- Failed password logins are throttled (5 per 15 minutes per client and e-mail, per API instance).
- Demo data is fictional and wiped nightly. The UI asks visitors not to enter real information.
- The OCR preview processes uploads in temporary storage and stores nothing.
- Netlify serves the SPA with CSP, `X-Frame-Options: DENY` and `nosniff`; the CSP allows API calls only
  to the page's own origin and `*.onrender.com`.

## Verification performed before deployment

- **Managed-database rehearsal (2026-10-01).** A local PostgreSQL 16 database whose owner is
  `NOSUPERUSER NOBYPASSRLS`, as on Neon, ran migrations to head, `alembic check`, the demo seed, two
  consecutive resets, the SLA sweep script and the full backend test suite. It found and fixed a real
  defect: the demo reset did not enable the RLS bypass, so on a non-superuser owner it would have seen no rows.
- **Production settings.** The API ran locally with the same environment as step 3, including a pasted
  libpq-style URL with `sslmode` and `channel_binding`. `/ready` was OK, `/meta` reported production with
  registration disabled, CORS allowed the configured web origin and refused another (400), persona login and
  authenticated reads worked, self-registration returned 403, and the log had no errors.
- **Container image.** The API Dockerfile (Tesseract, Poppler, `$PORT`) builds in GitHub Actions CI on
  every push. Running that image on Render is the first live check.
- Render, Netlify and Neon accounts themselves are created by the project owner, so they were not exercised here.
