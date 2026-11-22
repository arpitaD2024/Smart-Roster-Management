# Smart Roster Management

A Python roster planner using FastAPI, Streamlit, SQLAlchemy, SQLite or PostgreSQL, Pandas, and OR-Tools CP-SAT.

## Install and run locally

Run commands from the project root, which contains `alembic.ini`, `app.py`, and `requirements.txt`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item smart_roster\.env.example smart_roster\.env
```

Apply the initial schema and any pending migrations before launching the application:

```powershell
alembic upgrade head
```

Start the API and Streamlit UI in separate terminals, from that same project directory:

```powershell
uvicorn smart_roster.backend.main:app --reload
```

```powershell
streamlit run app.py
```

The API documentation is available at `http://localhost:8000/docs`.

On macOS/Linux, activate `.venv` with `source .venv/bin/activate`; use `cp smart_roster/.env.example smart_roster/.env` and `streamlit run app.py`.

## Database configuration and migrations

The default SQLite database (`smart_roster/data/roster.db`) is for local development only. Production requires PostgreSQL through `DATABASE_URL`; the application refuses to start in production without it or when configured with SQLite. Set `DATABASE_URL` in `smart_roster/.env` locally, or in your hosting provider's environment settings:

```env
DATABASE_URL=postgresql+psycopg://postgres:URL_ENCODED_PASSWORD@db.cjlzgofgrcbdjpzlrcjl.supabase.co:5432/postgres?sslmode=require
```

If a database password has been exposed in chat, logs, or source control, rotate it in Supabase before deployment and update the Render environment variable.

URL-encode any reserved characters in the database password. Never commit `.env` or paste a live password into source files. The API does not create tables at startup; Alembic migrations own the schema. The initial setup and Render start command use `alembic upgrade head`. After changing models, generate and review a migration, then apply it:

```powershell
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

To roll back one migration, use `alembic downgrade -1`. This changes the schema and can discard schema-level changes; back up the database before downgrading. Existing local SQLite databases can be selected with `DATABASE_URL=sqlite:///C:/absolute/path/to/file.db`, then upgraded with the same Alembic command. Migrations are additive for the current model changes and do not delete stored application records.

The configured local database is `smart_roster/data/roster.db`; it contains local application data and should not be included in a project ZIP or submission archive. The root `.gitignore` excludes database files, `.env`, `__pycache__` directories, and `.pyc` files.

## Deploy

### Streamlit Community Cloud

1. Deploy this repository and set the main file to `app.py` (the root entrypoint for the existing Streamlit UI).
2. Add `API_URL` as an environment variable or Streamlit secret with the HTTPS URL of the Render API, for example `https://smart-roster-api.onrender.com`. For Streamlit secrets, use a top-level `API_URL = "https://..."` setting.
3. The Streamlit app also reads a local `smart_roster/.env` file when running outside Community Cloud.

### Render API

1. Create a Render Blueprint from `render.yaml`, or create a Python web service from this repository.
2. Keep the configured `startCommand`; `PYTHONPATH=smart_roster` makes the existing package available as `backend.main`.
3. Set `DATABASE_URL` to the Supabase PostgreSQL connection string and `FRONTEND_URL` to the exact deployed Streamlit origin (for example `https://your-app.streamlit.app`). Add any additional comma-separated browser origins to `CORS_ORIGINS` if needed.
4. The start command applies pending Alembic upgrades before launching Uvicorn. It does not reset or recreate application data. Render's `/health` check confirms the API can connect to PostgreSQL.
5. Supabase's direct database hostname and port may require an IPv6-capable network. If the Render instance cannot reach it, use Supabase's supported session/transaction pooler URL and keep the PostgreSQL driver scheme `postgresql+psycopg://`.

The Render configuration marks `DATABASE_URL` and `FRONTEND_URL` as unsynced secrets/settings; set them in the Render dashboard before deployment. `APP_ENV=production` prevents accidental use of ephemeral local SQLite storage.

## First workflow

1. Add employees with a unique email and employee ID.
2. Add brands and processes.
3. Allocate employees as `PRIMARY` or `BACKUP`.
4. Add coverage rules for each brand/process pair.
5. Add employee time-off records and company holidays.
6. Generate a roster. Coverage gaps are permitted only when **Allow an explicitly incomplete draft** is selected.
7. Review validation, backup suggestions, and roster views; manually edit and lock entries if needed.
8. Regenerate a draft to preserve locked assignments, validate it, and publish only when valid.
9. Export the roster as CSV or Excel.

## Scheduling rules

- Calendars are `DAILY`, `MON_SAT`, or `CUSTOM` (weekday numbers Monday `0` through Sunday `6`).
- Weekly rules use Monday-Sunday calendar weeks, including across month boundaries.
- A partial week inside the requested roster range is scaled proportionally: required weekly offs are rounded up from `min_offs × included calendar days / 7`.
- Employee leave, comp-off, and full-closure company holidays make an employee unavailable and do **not** count as weekly offs.
- `max_days` limits consecutive workdays. `max_work_days` separately limits workdays in each Monday-Sunday week.
- Full-closure holidays skip coverage requirements and forbid assignments. A non-closure holiday is informational and does not close coverage.
- Incomplete-draft gap variables exist only when requested. A roster with any coverage gap fails validation and cannot be published.
- Solver responses report `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, or `UNKNOWN`; only a roster with no validation errors is considered valid.

## API capabilities

- Employee, brand, process, allocation, coverage, and time-off management.
- Company holiday management at `GET /holidays` and `POST /holidays`.
- Draft generation and regeneration, validation, publishing, entry editing, and entry locks.
- Backup suggestion and acceptance endpoints.
- Complete, brand, process, pod, and employee roster views with date/resource filters.
- CSV and Excel downloads from `/rosters/{roster_id}/export?format=csv` or `xlsx`.
- Employee and allocation CSV/XLSX imports.

## Tests

Run from the extracted project directory after installing requirements:

```powershell
python -m pytest smart_roster\tests -q
```

The test suite uses a temporary SQLite database and does not use or alter the local application database.

## Known limitations

- Authentication, role-based permissions, and Google Sheets OAuth are not included.
- Manual editing modifies an existing roster entry; it does not create new entries independently.
- Regeneration is intentionally limited to draft rosters. Published rosters cannot be regenerated or edited.
- A roster's consecutive-day checks are bounded to that roster's date range; assignments in separate rosters outside the range are not combined.
