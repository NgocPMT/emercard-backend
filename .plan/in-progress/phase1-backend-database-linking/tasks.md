# EmerCard Phase 1 Backend and Database Linking - Implementation Tasks

## Overview
Build the greenfield `emercard-backend/` foundation described in `01-phase1-initial-backend-database-linking-plan.md`. This packet resolves the stated defaults: a backend-only Python project using `uv`, `src/emercard`, an application factory, FastAPI, and PyMongo's official async API; Render and MongoDB Atlas remain the demo deployment targets. Confirm compatible patch versions before generating `uv.lock`; do not implement domain collections, authentication, or frontend code.

## Backend foundation
**chore(backend): scaffold managed Python backend project**
- [x] **1.** Create the backend-only Python project scaffold and reproducible developer tooling.
  - [x] **1.1** Add `pyproject.toml` with Python `3.14.6`, pinned direct runtime and development dependencies, `uv` configuration, Ruff, a Python type checker, pytest, and pytest-asyncio.
  - [x] **1.2** Create the `src/emercard/{api,core,db,modules}` and `tests/` package layout with only bootstrap placeholders; keep domain modules and collections absent.
  - [x] **1.3** Generate and retain `uv.lock` after confirming compatible stable patch versions for FastAPI, PyMongo 4.17.x async support, Pydantic v2, Pydantic Settings v2, Uvicorn, pytest, pytest-asyncio, Ruff, and the selected type checker.
  - [x] **1.4** Add `.gitignore` rules for `.env`, virtual environments, Python artifacts, test/type-check caches, and local MongoDB data; allow `.env.example` and `uv.lock`.

**feat(config): add validated application settings**
- [x] **2.** Centralize typed environment configuration in `src/emercard/core/config.py`.
  - [x] **2.1** Define validated settings for application metadata, environment (`local`, `test`, `demo`, `staging`, `production`), debug, API prefix, host/port, log level, optional build revision, and CORS origins/credentials.
  - [x] **2.2** Define MongoDB URI, database name, server-selection timeout, optional pool bounds, and TLS-required settings; fail fast for missing or invalid deployment values without logging secrets.
  - [x] **2.3** Add a committed `.env.example` with safe local defaults and documented placeholders for Atlas, frontend origins, and future auth secret configuration; never require real credentials in the repository.

## API and database lifecycle
**feat(api): bootstrap FastAPI infrastructure endpoints**
- [x] **3.** Implement an application factory and safe HTTP infrastructure boundary.
  - [x] **3.1** Create `create_app()` and the ASGI entry point with lifespan wiring, versioned `/api/v1` router registration, and direct `GET /health` liveness endpoint that never queries MongoDB.
  - [x] **3.2** Add CORS using configured allowlisted origins and credentials behavior; do not use a wildcard origin when credentials are enabled.
  - [x] **3.3** Add request ID generation/propagation and request logging limited to method, route, status, duration, and request ID; exclude bodies, cookies, authorization headers, MongoDB URIs, and secrets.
  - [x] **3.4** Add validation and unhandled-error handlers that return a documented non-sensitive envelope with error code, message, optional field details, and request ID; hide stack traces outside `local`.

**feat(db): manage async MongoDB readiness lifecycle**
- [x] **4.** Add the single managed PyMongo async client boundary and readiness behavior. (depends on: 2, 3)
  - [x] **4.1** Create `src/emercard/db` lifecycle code that creates one `AsyncMongoClient`, selects the configured database, exposes controlled access for later repositories, and closes the client during shutdown.
  - [x] **4.2** Implement `GET /ready` to ping MongoDB with the configured timeout and return a safe success response only when the database is reachable; return a non-sensitive service-unavailable response otherwise.
  - [x] **4.3** Keep connectivity verification readiness-driven so `/health` remains successful when MongoDB is unavailable and application startup/shutdown cannot hang on an unreachable database.
  - [x] **4.4** Add optional `GET /api/v1/meta` API/build metadata only if it can be populated solely from non-sensitive settings.

## Local containers and deployment
**build(containers): add reproducible local MongoDB and API runtime**
- [x] **5.** Provide host-run and Compose-run local environments.
  - [x] **5.1** Add `compose.yaml` with a verified MongoDB image, a persistent named volume, a health check, and an explicitly documented host port. (Plan baseline is 8.3; local compatibility fallback is MongoDB 8.2 on Linux kernel 6.19+)
  - [x] **5.2** Add a multi-stage `Dockerfile` for the API using the locked Python dependencies, non-secret runtime configuration, and platform-provided host/port binding.
  - [x] **5.3** Add the optional Compose API service with a service-name MongoDB URI and `depends_on` health gating; preserve the host-run localhost URI as a separate documented configuration.
  - [x] **5.4** Verify the API container starts and shuts down cleanly without embedding environment secrets or frontend tooling.

**chore(deploy): configure Atlas and Render demo connection boundary**
- [ ] **6.** Establish and document the non-code deployment configuration for the demo.
  - [ ] **6.1** Create the named MongoDB Atlas demo project/cluster, demo-only database, least-privilege database user, deployment-compatible network access policy, and a documented fictional-data reset procedure.
  - [x] **6.2** Add Render deployment configuration or documentation that installs from `uv.lock`, starts the ASGI app, binds the platform `PORT`, sets non-debug environment values, and injects Atlas/CORS values only as platform secrets.
  - [ ] **6.3** Configure the deployed frontend origin allowlist and confirm an unapproved browser origin is rejected by CORS; retain direct CORS calls as the Plan 01 integration approach.
  - [ ] **6.4** Record the final Atlas database name, service URL, environment names, and provider constraints in deployment documentation without committing credentials or connection strings.

## Testing and documentation
**test(infrastructure): cover configuration health and database failures**
- [x] **7.** Add automated foundation coverage without requiring live production infrastructure. (depends on: 2, 3, 4)
  - [x] **7.1** Add settings tests for valid local/test configuration and invalid or missing required deployment configuration, ensuring failure messages do not reveal secret values.
  - [x] **7.2** Add API tests proving `/health` succeeds without MongoDB and `/ready` returns a safe unavailable response when the MongoDB ping fails. (test: no live Atlas dependency)
  - [x] **7.3** Add readiness success coverage using a controlled async client/ping test double or isolated test database. (test: `GET /ready` returns success only after a successful ping)
  - [x] **7.4** Add tests for CORS allowlisting, request-ID response/error propagation, and validation/server error envelopes with no stack trace in non-local environments.
  - [x] **7.5** Run `uv run ruff check .`, the configured formatter check, the configured type checker, and `uv run pytest`; fix all findings before deployment verification.

**docs(backend): document local and deployed smoke verification**
- [ ] **8.** Document installation, operations, and the Plan 01 handoff contract. (depends on: 5, 6, 7)
  - [x] **8.1** Write `README.md` instructions for `uv` installation/sync, `.env` setup, direct host-run API, MongoDB-only Compose, full Compose, lint/type/test commands, and the different host versus container MongoDB URIs.
  - [x] **8.2** Document `/health`, `/ready`, optional `/api/v1/meta`, error-envelope behavior, CORS configuration, safe logging rules, and curl smoke commands for healthy and unavailable database states.
  - [x] **8.3** Run and record the local smoke path: HTTP client → FastAPI → `/ready` → MongoDB ping; also confirm `/health` remains available with MongoDB stopped.
  - [ ] **8.4** Run and record deployed HTTPS smoke checks for `/health` and `/ready` against Atlas, inspect logs for secret leakage, and hand off the final structure/config keys/service names/platform constraints to Plan 02.

## Completion Summary

| Section | Completed | Total | Status |
|---|---:|---:|---|
| Backend foundation | 9 | 9 | Complete |
| API and database lifecycle | 10 | 10 | Complete |
| Local containers and deployment | 6 | 10 | In progress |
| Testing and documentation | 9 | 11 | In progress |
| **Overall** | **34** | **40** | **In progress** |
