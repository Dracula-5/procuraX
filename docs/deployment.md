# Free deployment (no credit card)

Total cost: ¥0. Three free accounts, all created without a payment card:

| Piece | Service (free tier) | What you get | Limits that matter |
|---|---|---|---|
| Database | [Neon](https://neon.tech) Free | PostgreSQL 16, 0.5 GB, permanent | Compute sleeps after 5 idle minutes (sub-second wake); limited connections |
| API | [Render](https://render.com) Free web service (Docker) | FastAPI container with Tesseract OCR | Sleeps after 15 idle minutes; the first request then takes about a minute; 512 MB RAM |
| Web app | Render Free static site | The React build on a CDN | None relevant |
| Schedules | GitHub Actions | Migrations on release, hourly SLA sweep, nightly demo reset | Scheduled workflows pause after 60 days without repository activity |

Render's own free PostgreSQL is not used because it expires after 30 days; Neon's free database
does not. Free-tier terms change, so check each provider's pricing page before relying on them.

## 1. Put the repository on GitHub

Create a GitHub repository (public keeps Actions minutes unlimited) and push this project to `main`.
CI runs on every push. Until the secrets below exist, the deploy and maintenance workflows log
"not configured" and do nothing.

## 2. Create the Neon database

1. Sign up at neon.tech → **New project** → PostgreSQL **16**, region **AWS Asia Pacific (Singapore)**
   (closest to Render's Singapore region).
2. Open the **SQL Editor** and create the least-privilege runtime role. Use your own strong password.

   ```sql
   CREATE ROLE procurax_app LOGIN PASSWORD 'replace-with-a-long-random-password' NOSUPERUSER NOBYPASSRLS;
   GRANT CONNECT ON DATABASE neondb TO procurax_app;
   ```

3. Under **Connect**, turn **off** connection pooling (asyncpg's prepared statements need the direct
   host) and copy the connection string. Paste Neon's string exactly as shown: the backend
   converts `postgresql://…?sslmode=require&channel_binding=require` to the asyncpg form itself.
   You need two strings:

   - Owner (migrations, demo seed and reset): the string Neon shows for `neondb_owner`.
   - Runtime (the API): the same string with the user and password replaced by
     `procurax_app` and the password you chose. Percent-encode special characters in the password,
     for example `@` becomes `%40`.

The Neon owner is **not** a superuser, so forced row-level security applies to it too. The
migrations, the demo reset and the full test suite were rehearsed locally against exactly that
setup; see "Rehearsal" below.

## 3. Create the Render services

1. Sign up at render.com with your GitHub account → **New → Blueprint** → pick the repository.
   Render reads [`render.yaml`](../render.yaml) and proposes `procurax-api` and `procurax-web`.
2. Fill in the values it asks for:
   - `PROCURAX_DATABASE_URL`: the **runtime** URL from step 2.
   - `PROCURAX_PUBLIC_APP_URL`: `https://procurax-web.onrender.com`. This is also the only allowed
     CORS origin. If Render assigns a different hostname, update it after the first deploy.
   - `VITE_API_BASE_URL` (web): `https://procurax-api.onrender.com/api/v1`. It is built into the
     JavaScript, so after changing it, redeploy the web service.
3. On each service, open **Settings → Deploy Hook** and copy its URL.

## 4. Add GitHub secrets, then release

Repository → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Value |
|---|---|
| `NEON_OWNER_DATABASE_URL` | Owner URL from step 2 |
| `NEON_APP_DATABASE_URL` | Runtime URL from step 2 |
| `RENDER_API_DEPLOY_HOOK` | API deploy hook |
| `RENDER_WEB_DEPLOY_HOOK` | Web deploy hook |

Then run **Actions → Deploy → Run workflow**. It runs `alembic upgrade head` as the owner (the
release step), creates the fictional demo company if it does not exist yet, then triggers both
Render deploys. Render only switches traffic once `/ready` returns OK.

From then on, every push to `main` that passes CI migrates and redeploys. The demo tenant resets
at 03:00 JST, and the SLA sweep runs hourly even while the API sleeps.

## 5. Check it

- Open the web URL. The first load after a quiet period shows "Waking up the free-tier API" for up to a minute.
- The public-demo banner is visible and **Create organisation** is hidden. In public demo mode,
  self-registration is off so anonymous visitors cannot store their own data.
- Sign in as any persona from the login page; [user_personas.md](user_personas.md) suggests
  walkthroughs for each.
- `https://<api>/api/v1/meta` returns `"public_demo": true`.

## If something does not work

| Symptom | Likely cause | Fix |
|---|---|---|
| Web shows "API is not reachable" | `VITE_API_BASE_URL` wrong or not rebuilt | Correct it on `procurax-web`, then redeploy the web service |
| Browser console shows a CORS error | `PROCURAX_PUBLIC_APP_URL` does not match the web URL exactly | Set it to the web URL (https, no trailing path) and redeploy the API |
| API deploy fails on `/ready` | Wrong runtime DB string, or `procurax_app` missing | Re-check step 2; the Render log shows the database error |
| Login page shows no personas | Demo tenant not created | Run *Demo maintenance* with *Reset the demo tenant* ticked |
| Deploy workflow says "not configured" | GitHub secrets missing | Add the secrets in step 4 |

## How the public demo is protected

- `PROCURAX_PUBLIC_DEMO=true` is the only way production settings accept demo mode. Persona login
  works only for the organisation flagged `is_demo`, and every persona login is audited.
- The JWT secret is generated by Render and never stored in the repository.
- Failed password logins are throttled (5 per 15 minutes per client and e-mail, per API instance).
- Data in the demo tenant is fictional and wiped nightly. The UI asks visitors not to enter real information.
- The OCR preview processes uploads in temporary storage and stores nothing.

## Rehearsal performed before writing this guide

On 2026-10-01 a local PostgreSQL 16 database was created whose owner is `NOSUPERUSER NOBYPASSRLS`,
as on Neon. Against it, migrations to head, `alembic check`, the demo seed, two consecutive resets,
the SLA sweep script and the full backend test suite were run; results are in
[local_verification.md](local_verification.md). The rehearsal found and fixed a real defect: the
demo reset's existence check and delete step did not enable the RLS bypass, so on a
non-superuser owner they would have seen no rows. Render and Neon themselves were not exercised,
because they need your accounts.

The API was also started locally with exactly the environment `render.yaml` sets: `PROCURAX_ENV=prod`,
public demo, and a pasted libpq-style URL with `sslmode` and `channel_binding`. `/ready` reported the
database OK and `/api/v1/meta` showed production with registration disabled. A CORS preflight from
`https://procurax-web.onrender.com` was allowed and one from another origin was refused (400).
Persona login and authenticated reads worked, self-registration returned 403, and the log had no errors.
The frontend was built with `VITE_API_BASE_URL` set and the URL was confirmed in the bundle.

**Not yet verified:** the updated API image (Tesseract, Poppler, `$PORT`) could not be rebuilt here
because Docker Desktop stopped responding when the disk filled. The Render build will be the first
build of that Dockerfile; if it fails, the Render build log shows the failing step.
