# Context Pack

## Spec source
- Active plan: `.plan/in-progress/phase1-backend-database-linking/tasks.md`
- Source specification: `../../../../01-phase1-initial-backend-database-linking-plan.md` (workspace root)
- Current task: implement the Phase 1 runnable FastAPI, MongoDB, local-container, and demo deployment foundation.

## Current goal
- Create a backend-only, deployable foundation before Plan 02 defines domain models. Use one managed PyMongo official async client, typed settings, `/health`, `/ready`, safe infrastructure behavior, and a documented local/Atlas path.

## Affected repos / packages
| Path | Role | Why it matters |
|---|---|---|
| `emercard-backend/` | Target backend repository | Currently empty; all implementation artifacts belong here. |
| `../../../../01-phase1-initial-backend-database-linking-plan.md` | Governing Plan 01 specification | Defines scope, security rules, decision gates, exit criteria, and version baseline. |
| `../obra-social-landing/` | Unrelated frontend repository | Do not add source, tooling, or monorepo coupling here. Integration is HTTP/CORS only. |

## Bounded context / domain language
- Context files consulted: workspace `AGENTS.md`; Plan 01 source specification. No backend-local guidance or existing source is present.
- Canonical terms: **liveness** (`/health`, process-only), **readiness** (`/ready`, MongoDB ping), **demo data** (fictional only), **Atlas** (deployed MongoDB), **local MongoDB** (Compose service), **host-run** vs **container-run** API.
- Terms to avoid or disambiguate: do not call `/health` a database check; do not label Phase 1 safe for real medical data; do not refer to Motor (explicitly excluded).

## Files to inspect first
| File | Why |
|---|---|
| `../01-phase1-initial-backend-database-linking-plan.md` | Authoritative requirements and non-goals. |
| `pyproject.toml` | Create first; it defines package manager, pins, scripts, lint/type/test settings. |
| `src/emercard/main.py` | Application factory/ASGI entrypoint and lifespan composition. |
| `src/emercard/core/config.py` | Single settings source for all environment-dependent values. |
| `src/emercard/db/*` | The sole async MongoDB client lifecycle and readiness access. |
| `compose.yaml`, `Dockerfile`, `.env.example`, `README.md` | Local/deployment contract and verification instructions. |
| `tests/` | Isolate settings, health, readiness, CORS, and error tests from live Atlas. |

## Important interfaces / contracts
- Public APIs:
  - `GET /health`: success if the FastAPI process is alive; it must not contact MongoDB.
  - `GET /ready`: success only if a configured MongoDB ping succeeds; return non-sensitive service-unavailable failure otherwise.
  - Optional `GET /api/v1/meta`: non-sensitive app/build metadata only.
- Types / schemas:
  - One Pydantic Settings object for app, database, CORS, and deployment values.
  - Error responses contain error code, human-readable message, optional field details, and request ID.
- Events / messages: none in Plan 01.
- Routes / commands:
  - Reserve `/api/v1` for later feature routers; infrastructure endpoints stay direct as specified.
  - Use `uv` commands from the backend root; exact project-script names are established in Task 1.
- Database:
  - Use PyMongo 4.17.x official async API (`AsyncMongoClient`), never Motor.
  - One central client lifecycle; feature modules must not construct clients.
  - No domain collections, indexes, repositories, or auth data in this plan.

## Project conventions
- Naming: Python packages under `src/emercard`; isolate `api`, `core`, `db`, and later `modules` boundaries.
- Dependency policy: Python 3.14.6; pin direct dependencies after checking compatible current patch versions; commit `uv.lock`; do not introduce new major versions during Phase 1.
- Testing: pytest + pytest-asyncio; test settings and database outcomes using controlled failures/test doubles or isolated test infrastructure, never deployed Atlas.
- Error handling: avoid stack traces outside `local`; preserve FastAPI/OpenAPI usefulness; return the common safe envelope where applicable.
- Data handling: secrets only in local `.env` or platform environment variables; never log URIs, credentials, auth headers, cookies, bodies, or medical fields.

## Verification commands
### Root
- No root-level command applies: this workspace root is not a Git repository and the backend is currently an empty directory.

### Per repo / package
- `emercard-backend/` (after Task 1 establishes the project):
  - `uv sync --all-groups`
  - `uv run ruff check .`
  - Run the formatter check configured in `pyproject.toml`.
  - `uv run <configured-type-checker>`
  - `uv run pytest`
  - `docker compose up -d mongodb`
  - `docker compose up --build`
  - curl `/health` and `/ready` with MongoDB both available and stopped.

## Do not touch
- Do not modify `../obra-social-landing/`.
- Do not add frontend source/tooling, domain collections/models, auth endpoints, password handling, profile CRUD, public links, Redis, email/SMS, refresh tokens, rate limiting, encryption, cards/scans/audits/admin features, or index initialization.
- Do not commit `.env`, Atlas URI/credentials, deployment secrets, or real medical information.
- Do not add Phase 2 infrastructure or claim Phase 1 is production-safe for real medical data.

## Known risks
- `emercard-backend/` contains no existing code, package manifest, tests, repository metadata, or local instructions; all inferred conventions are explicitly resolved defaults from Plan 01.
- Python 3.14 and the specified library baseline require a compatibility check before dependency pins and lockfile generation.
- Atlas free-tier labels/availability, Render behavior, and allowed network access can change; verify them during Task 6 rather than hard-code provider assumptions.
- Host-run and container-run MongoDB URIs differ; test/document both to prevent false readiness failures.
- The Plan 01 8.3 image exits on this host's Linux kernel 6.19+ (SERVER-121912); `mongo:8.2` starts successfully and is the local Compose fallback until the runtime changes.
- Direct cross-origin frontend calls require precise CORS origins and credentials behavior; no wildcard with credentials.

## Open questions
- Confirm the final deployed frontend URL(s) before finalizing CORS environment values.
- Confirm access to an Atlas account/project and Render account before Task 6; if unavailable, complete local checks and document deployment as blocked rather than using placeholder secrets.
- Confirm the desired Python type checker (Pyright is the likely default) while creating `pyproject.toml`.

## Recommended implementation entry point
1. Complete Task 1 from the backend root: create `pyproject.toml`, the `src/` layout, and lockfile/tooling before any API code.
2. Complete Task 2: define and test typed settings plus `.env.example`; it is the only source of environment values.
3. Complete Tasks 3 and 4 together: wire the app factory/lifespan to the single async client, then test liveness and readiness failure/success behavior before adding containers.
