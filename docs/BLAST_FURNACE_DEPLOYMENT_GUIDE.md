# Blast Furnace Digital Twin - deployment guide

Extractmet Private Limited - Blast Furnace Digital Twin, Process Simulator and Advisory Control
(demonstration build, synthetic data). Two GitHub repositories, deployed on Render.

> **Read first.** This build runs on synthetic data and gives advice only. It has no connection
> to, and no way to write to, any plant system. Do not connect a cloud deployment to a plant
> network. For a site pilot follow `docs/OT_SECURITY.md` (on-premises, read-only data path).

## 1. What you deploy

| Repository | Render service | What it is |
|---|---|---|
| `blast-furnace-backend` | Web service (Python) | FastAPI API, physics twin, observer, forecasts, advisory MPC, scenarios, reports |
| `blast-furnace-frontend` | Static site | The operator desk (HTML, CSS, JavaScript, Three.js, Plotly), built by Vite |

The browser loads the static site and calls the backend over HTTPS (REST and a WebSocket
stream). The backend URL is baked into the frontend at build time (`VITE_API_BASE_URL`), and the
backend only accepts browser calls from the frontend URL listed in `CORS_ORIGINS`.

Order of work: **backend first** (to get its URL), **frontend second** (to get its URL), then put
the frontend URL into the backend's `CORS_ORIGINS`.

## 2. What you need

- A GitHub account (free is fine). Private repositories are recommended.
- A Render account (https://render.com), signed in with GitHub so Render can read the repositories.
- To upload with the command line: Git installed (https://git-scm.com). The GitHub web upload
  (section 3, option B) needs nothing installed.
- Only for running on your own computer (optional, section 9): Python 3.13 and Node.js 22.

## 3. Put the code on GitHub

Unzip `blast-furnace-backend.zip` and `blast-furnace-frontend.zip` into two separate folders.

### Option A - command line (recommended)

Create two **empty** repositories on GitHub (New repository; do not add a README, .gitignore or
licence): `blast-furnace-backend` and `blast-furnace-frontend`. Then, in a terminal:

```bash
cd blast-furnace-backend
git init -b main
git add .
git commit -m "Blast furnace digital twin backend"
git remote add origin https://github.com/<your-account>/blast-furnace-backend.git
git push -u origin main

cd ../blast-furnace-frontend
git init -b main
git add .
git commit -m "Blast furnace digital twin frontend"
git remote add origin https://github.com/<your-account>/blast-furnace-frontend.git
git push -u origin main
```

Each repository already contains a `.gitignore`, so local virtual environments, `node_modules`,
`.env` files and databases are never committed.

### Option B - GitHub web upload

On each new repository page choose **uploading an existing file**, drag the *contents* of the
unzipped folder (not the folder itself) into the page and commit. Hidden files matter: make sure
`.gitignore`, `.python-version` (backend), `.node-version` and `.npmrc` (frontend) and the
`.github` folder (automated tests, section 8) are uploaded; on macOS press Cmd+Shift+. in Finder
to show them. GitHub's web upload accepts up to 100 files per
batch; upload the `app`, `docs`, `src` and `tests` folders in separate batches if needed.

Check on GitHub that `render.yaml` sits at the top level of each repository.

## 4. Deploy the backend

1. In Render choose **New > Blueprint** and select the `blast-furnace-backend` repository.
2. Render reads `render.yaml` and shows one web service, `bf-twin-backend`. It asks for the
   variables marked `sync: false`:
   - `CORS_ORIGINS`: enter `https://bf-twin-frontend.onrender.com` for now; you will correct it
     in section 6 once you know the real frontend URL.
   - `EDGE_INGEST_TOKEN`: leave empty (only for an on-premises read-only gateway).
3. Click **Apply**. The first build installs the Python packages (a few minutes).
4. When the service shows **Live**, copy its URL from the top of the service page, for example
   `https://bf-twin-backend.onrender.com` (Render may add a suffix if the name is taken).
5. Check it in the browser:
   - `<backend-url>/health` returns `{"status":"ok",...}`;
   - `<backend-url>/ready` returns `"ready": true` within about a minute (the twin loads its
     shipped initial state; on the free plan allow longer);
   - `<backend-url>/docs` shows the interactive API documentation.
6. Open **Environment** on the service page and note the generated value of
   `DEMO_USER_PASSWORD`. This is the password of the demo accounts. Do not paste it into any file
   in the repository.

The blueprint sets: `APP_ENV=production`, `DEMO_MODE=true`, generated `JWT_SECRET` and
`DEMO_USER_PASSWORD`, `PLANT_PRESET=large_4000`, `SIM_SPEED=30`, `ALLOW_PLANT_WRITES=false`
(the service refuses to start if this is ever set to true), health check `/health`, automatic
redeploy on every commit, region Singapore, plan free.

## 5. Deploy the frontend

1. In Render choose **New > Blueprint** and select the `blast-furnace-frontend` repository.
2. Render shows one static site, `bf-twin-frontend`, and asks for `VITE_API_BASE_URL`. Enter the
   backend URL from section 4 step 4, **with `https://` and without a trailing slash**.
3. Click **Apply**. Render runs `npm ci && npm run build` and publishes `./dist`.
4. When it is **Live**, copy the site URL, for example `https://bf-twin-frontend.onrender.com`.

The blueprint also sets the security headers (CSP, X-Frame-Options, nosniff, Referrer-Policy,
Permissions-Policy) and a rewrite to `index.html`.

## 6. Connect the two

1. Open the backend service in Render > **Environment**.
2. Set `CORS_ORIGINS` to the exact frontend URL from section 5 step 4 (`https://...`, no trailing
   slash). Several origins can be separated by commas (for example a custom domain as well).
3. Save. Render redeploys the backend (about a minute).
4. Open the frontend URL. The status strip should show *Synthetic demo data*, a green data
   clock, *Model healthy* and *Balance closed* within a minute.

If you later change the backend URL, set `VITE_API_BASE_URL` on the frontend again and
redeploy it (Manual Deploy > Deploy latest commit): the value is read at build time.

## 7. Sign in and check

Viewing works without signing in. To use the controls, click **Viewer (sign in)** at the top right:

| Username | Role | Can do |
|---|---|---|
| `viewer` | VIEWER | look at every display |
| `operator` | OPERATOR | request, acknowledge or reject advice; acknowledge alarms; demo setpoints and fault injection |
| `metallurgist` | METALLURGIST | what-if scenarios, studies, data upload |
| `engineer` | PROCESS_ENGINEER | furnace design and configuration, presets, tag mapping, ROM identification, model training, modes |
| `admin` | ADMIN | everything, including the audit trail |

All demo accounts use the `DEMO_USER_PASSWORD` value from section 4 step 6. Demo accounts are
created only when that value has at least 10 characters.

Acceptance check (about ten minutes):

1. Plant overview: tiles update every few seconds; the schematic and trends are filled.
2. Furnace 3D: the cut-away furnace renders and can be rotated.
3. Advisory MPC (as `operator`): **Compute advisory** returns a recommendation with trust-gate
   checks, moves, trajectories and a physics verification.
4. What-if scenarios (as `metallurgist`): run **S03**; the result shows baseline and case
   trajectories, final deltas, cumulative inputs/outputs and compositions.
5. Inputs and outputs: download the streams CSV; its header states *SYNTHETIC* and the versions.
6. Furnace design and configuration (as `engineer`): switch to another preset and back.

## 8. Plans, persistence and updates

- **Free plan**: the backend sleeps after 15 minutes without traffic and takes about a minute to
  wake; its disk is temporary, so the SQLite database (configuration versions, audit records,
  recommendations) is reset on every restart or redeploy. Good for trying the system.
- **Client demonstrations**: use at least the Starter instance for the backend (no sleeping,
  more CPU). Static sites are free.
- **Keeping data**: create a Render Postgres database and set `DATABASE_URL` on the backend to its
  internal connection string, or uncomment the `databases` block and the `DATABASE_URL` entry in
  `render.yaml` and re-sync the blueprint. Free Render Postgres databases expire after 30 days.
- **Updates**: push a commit to `main`; Render redeploys automatically.
- **Automated tests on GitHub**: each repository has `.github/workflows/ci.yml`. On every push,
  GitHub Actions runs the tests with coverage (backend about 10 minutes, frontend under 2), the
  static build, and dependency and static-security checks; results are on the repository's
  **Actions** tab. To let Render deploy only after these checks pass, change
  `autoDeployTrigger: commit` to `autoDeployTrigger: checksPass` in `render.yaml` (or Settings >
  Auto-Deploy > *After CI checks pass*).
- **Custom domain**: Settings > Custom Domains on each service; then add the new frontend
  origin to `CORS_ORIGINS` and, if the backend domain changes, rebuild the frontend.

## 9. Run on your own computer (optional)

Backend (Python 3.13):

```bash
cd blast-furnace-backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DEMO_USER_PASSWORD=choose-a-long-password      # Windows PowerShell: $env:DEMO_USER_PASSWORD="..."
export CORS_ORIGINS=http://localhost:5173
uvicorn app.main:app --reload --port 8000
```

Frontend (Node.js 22), in a second terminal:

```bash
cd blast-furnace-frontend
npm ci
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev                          # opens http://localhost:5173
```

Tests:

```bash
cd blast-furnace-backend && pip install -r requirements-dev.txt
pytest -q                            # about 8 minutes on 2 CPUs; add --cov=app for coverage
cd ../blast-furnace-frontend
npm test                             # unit and display smoke tests; npm run coverage for coverage
```

`docs/TEST_RESULTS.md` in each repository records the last full run. `docs/UAT_CHECKLIST.md`
(frontend) is the manual acceptance list for a demonstration or pilot.

Container (on-premises evaluation host):

```bash
cd blast-furnace-backend
docker build -t bf-twin-backend .
docker run -p 8000:8000 -e DEMO_USER_PASSWORD=choose-a-long-password \
  -e CORS_ORIGINS=http://localhost:5173 bf-twin-backend
```

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Status strip stays on *Connecting to backend ...* or shows *Failed to fetch* | Backend asleep (free plan) - wait a minute and reload; or `VITE_API_BASE_URL` is wrong - fix it and redeploy the frontend |
| Browser console: *blocked by CORS policy* | `CORS_ORIGINS` on the backend does not exactly match the frontend URL (scheme, no trailing slash) |
| `/ready` returns 503 for a while | Twin still initialising (shipped state loads in seconds; first sensitivity run takes longer on small instances); wait |
| Sign-in fails | Use the `DEMO_USER_PASSWORD` value from the backend's Environment tab; it is regenerated only if you delete the variable |
| Strip shows *polling* with an amber dot | WebSockets are blocked by a proxy; the desk falls back to polling automatically |
| Backend build fails on the Python version | Keep `PYTHON_VERSION` fully qualified (for example `3.13.18`); Render supports released versions only |
| Frontend build fails in `npm ci` | Make sure `package-lock.json` and `.npmrc` were uploaded to GitHub |
| Configuration or audit history disappeared | Free-plan disk is temporary; attach Postgres (section 8) |
| Advice says ADVICE_SUPPRESSED | A trust-gate check failed (data quality, freshness, balance, alarm); the reasons are listed |

## 11. Security notes

- `VITE_*` variables are compiled into public JavaScript: never put a secret there.
- Secrets (`JWT_SECRET`, `DEMO_USER_PASSWORD`, `EDGE_INGEST_TOKEN`) live only in Render's
  Environment settings; the repositories contain none.
- Keep `ALLOW_PLANT_WRITES=false`. There is no supported way to send setpoints to a plant.
- With `DEMO_MODE=false` the backend requires a `JWT_SECRET` of at least 32 characters and does
  not create demo accounts.
- For real plant data: on-premises deployment, read-only edge collector, site SSO; see
  `docs/OT_SECURITY.md` in the backend repository.
