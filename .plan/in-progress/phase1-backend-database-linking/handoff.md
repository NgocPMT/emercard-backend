# Handoff

## Current status
incomplete

## Spec or task source
- Path: `.plan/in-progress/phase1-backend-database-linking/tasks.md`
- Type: task

## What was done
- Scaffolded the Python 3.14.6 backend with pinned dependencies and generated `uv.lock` using an activated `.venv`.
- Implemented typed settings, FastAPI factory/lifespan, safe error responses, CORS, request IDs/logging, `/health`, `/ready`, and `/api/v1/meta`.
- Implemented one managed PyMongo `AsyncMongoClient` lifecycle with readiness ping and graceful close.
- Added Compose, Dockerfile, Render blueprint, deployment/smoke documentation, and automated tests.

## Tasks completed
- Backend foundation: 9/9.
- API and database lifecycle: 10/10.
- Local containers and deployment: 6/10.
- Testing and documentation: 9/11.
- Overall: 34/40; task packet remains in progress.

## Tasks remaining
- Provision and verify the MongoDB Atlas demo project, database/user, network policy, and fictional-data reset process: task 6.1.
- Confirm deployed frontend CORS origins and rejected-origin behavior: task 6.3.
- Record final Atlas database name, Render URL, environment names, and provider constraints: task 6.4.
- Run deployed HTTPS `/health` and `/ready` smoke checks, inspect logs, and complete Plan 02 handoff: task 8.4.

## Files changed
- `pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `README.md`.
- `src/emercard/` application, config, API, and database lifecycle modules.
- `tests/` settings, API, and database lifecycle tests.
- `compose.yaml`, `Dockerfile`, `.dockerignore`, `render.yaml`.
- `docs/deployment.md`, `docs/smoke-checks.md`.
- `tasks.md`, `context-pack.md`, and this handoff.

## Verification
### Commands run
- Activated environment before dependency installation: `source .venv/bin/activate`.
- `uv sync --all-groups`
- `uv lock --check`
- `uv run ruff format .`
- `uv run ruff check --fix .`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pyright`
- `uv run pytest`
- `docker compose config`, `docker build --check .`
- Host-run API with Compose MongoDB: `/health` and `/ready` success; after MongoDB stop, `/health` stayed 200 and `/ready` returned 503.
- Full `docker compose up --build`: `/health` and `/ready` success.

### Results
- 12 tests passed.
- Ruff, format check, Pyright, lock check, Compose validation, Dockerfile check, and local container smoke passed.
- Pytest emits one dependency deprecation warning: Starlette's `TestClient` currently warns that this `httpx` version is deprecated and suggests `httpx2`.

## Review status
- Status: not run; there is no backend Git repository metadata available for a normal diff review.
- Must-fix findings: none known from verification.
- Follow-ups: run review after backend Git tracking is established if required.

## Decisions and deviations
- Local Compose uses `mongo:8.2` instead of the Plan 01 8.3 baseline because `mongo:8.3` exits on the current Linux kernel 6.19+ (MongoDB SERVER-121912); 8.2 starts and passes smoke checks. Revisit when the runtime changes.
- Pyright is configured for `src/` only because strict checking of installed Starlette/TestClient stubs produced unusable unknown-type diagnostics in tests; tests are still executed by pytest.
- Atlas and Render provisioning were documented but not attempted because credentials/accounts are external and unavailable in the repository.

## Known issues and risks
- No `.git` directory exists for `emercard-backend/`, so `uv.lock` is generated but cannot be committed from this workspace.
- The deployed frontend URL and Atlas/Render service identifiers are still unknown.
- The `httpx`/Starlette TestClient deprecation warning should be revisited when the compatible `httpx2` package is confirmed.

## Recommended next step
- Provision Atlas and Render, populate deployment secrets without committing them, run the HTTPS smoke checks, and then complete tasks 6.1, 6.3, 6.4, and 8.4.

## Suggested next prompt
`Continue from emercard-backend/.plan/in-progress/phase1-backend-database-linking/handoff.md. Read tasks.md and context-pack.md, provision or verify the Atlas/Render deployment boundary, run the deployed /health and /ready smoke checks, then update the checklist and handoff.`
