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

The API address is set in `netlify.toml` (`VITE_API_BASE_URL`, currently
`https://procurax-mod8.onrender.com/api/v1`) together with a CSP that allows only that origin. It is not a
secret. If your Render URL differs, change both lines in `netlify.toml` and push; Netlify rebuilds.
Then set the API's `PROCURAX_PUBLIC_APP_URL` (step 3) to the exact Netlify URL (`https://…netlify.app`,
no trailing slash). It is also the only browser origin the API's CORS policy allows.

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

- `https://<render-api>/ready` returns `{"status":"ready","database":"ok"}`.
- `https://<render-api>/api/v1/meta` returns `"public_demo": true` and `"registration_enabled": false`.
- Open the Netlify URL. The first load after a quiet period shows "Waking up the free-tier API" for up
  to a minute; then the public-demo banner appears and **Create organisation** is hidden.
- Sign in as any persona on the login page; [user_personas.md](user_personas.md) has walkthroughs.

## If something does not work

| Symptom | Likely cause | Fix |
|---|---|---|
| Render: `failed to read dockerfile` / not found | Dockerfile Path includes `backend/` twice | Set it to `./Dockerfile` with Root Directory `backend` |
| Render log: `PROCURAX_JWT_SECRET must be set` or `…must use HTTPS` | Environment variables missing | Complete the step 3 Environment table |
| Render deploy fails on `/ready` | Wrong runtime DB string, or `procurax_app` missing | Re-check step 2; the Render log shows the database error |
| API errors such as `relation "…" does not exist` | Migrations not run | Add the step 5 secrets and run **Actions → Deploy** |
| Web shows "API is not reachable" | `VITE_API_BASE_URL` in `netlify.toml` points elsewhere | Fix it (and the CSP line) in `netlify.toml` and push |
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
