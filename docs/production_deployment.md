# Production Deployment

This project keeps local development and production deployment paths separate.

## Local Development

Use the default compose file for fast iteration:

```powershell
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

The development compose file uses source bind mounts, Vite dev server, and backend reload mode.

## Production Compose

Start from the production template:

```powershell
Copy-Item .env.production.example .env
```

Replace every placeholder secret, origin, and API key in `.env`. The backend, worker, and Beat refuse to start in `APP_ENV=production` when default or placeholder credentials are still present.

The standard workflow keeps `APP_ENV_FILE=../.env`, which is resolved relative to `infra/docker-compose.prod.yml`. If you intentionally deploy with another `--env-file`, override `APP_ENV_FILE` to the same file's relative path; otherwise Compose interpolation and the application services can read different configurations.

Validate the production compose configuration:

```powershell
docker compose -f infra/docker-compose.prod.yml --env-file .env config --quiet
```

Deploy:

```powershell
docker compose -f infra/docker-compose.prod.yml --env-file .env up --build -d
```

The production compose file:

- serves the frontend through Nginx static assets, not Vite dev server;
- renders the Nginx API prefix, multipart body limit, and upstream timeout from `API_PREFIX`, `NGINX_MAX_BODY_SIZE_MB`, and `NGINX_PROXY_TIMEOUT_SECONDS`; proxies API traffic to the backend and disables response buffering for SSE;
- resolves the backend through Docker DNS at runtime so a recreated backend container does not leave Nginx pinned to a stale IP;
- starts the backend without reload mode;
- keeps the backend port on the internal Compose network and publishes only the Nginx frontend;
- runs Alembic migrations through the one-shot `migrate` service before backend and worker start;
- runs one dedicated Celery Beat service for memory-job, deferred-turn, conversation-summary, external-cleanup recovery, and scheduled retention;
- sets `APP_ENV=production` and `AUTO_CREATE_TABLES=false`;
- uses restart policies and container health checks.

Do not omit the `beat` service when deploying asynchronous memory updates. Run exactly one Beat scheduler for a deployment; starting Beat inside every worker (for example with `worker -B`) causes duplicate periodic dispatches.

## Operational Notes

- The Celery worker processes document indexing, asynchronous memory updates, memory reconcile tasks, and external cleanup jobs.
- Celery Beat redispatches memory jobs with expired processing leases, missing dispatch records, or explicit broker dispatch failures. Dispatch claims are fenced so consecutive scans do not create a delivery storm.
- Beat also recovers completed Agent runs whose assistant message was committed before the deferred memory job could be created. Memory jobs are unique per user message, so recovery is idempotent.
- Conversation summaries run as Celery tasks and use a token-fenced Redis lease per conversation, preventing concurrent deliveries from issuing duplicate LLM calls. Beat scans persisted summary cursors and redispatches eligible conversations, so a lost in-process or broker dispatch is recovered without losing committed messages.
- Document, knowledge-base, and permanent memory deletion record an `external_cleanup_jobs` task for Qdrant points and MinIO objects. Cleanup jobs use atomic leases; queued, failed, and expired-processing jobs are recovered automatically and mirrored to audit logs.
- Admins can inspect and retry cleanup tasks through `GET /api/admin/external-cleanup-jobs` and `POST /api/admin/external-cleanup-jobs/{job_id}/retry`.
- Operational retention is configurable through `*_RETENTION_DAYS` settings. Admins can preview or apply it from the Admin Retention tab or through `POST /api/admin/retention/run?dry_run=true|false`.
- Production conversation serialization is fail-closed: when Redis is unavailable, a new turn returns `503` instead of falling back to a process-local lock that is unsafe across multiple Uvicorn workers.
- `AGENT_STREAM_MAX_CONCURRENCY=8` caps active streaming Agent workers per Uvicorn process. With the production image's two Uvicorn workers, the default host-level ceiling is 16; tune it against provider quotas, database pool size, and available memory.
- `VITE_API_BASE_URL` is compiled into the frontend image. Rebuild the image after changing it; restarting an existing container is not sufficient.
- The bundled MinIO service currently uses `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` as root credentials. A hardened deployment should provision separate least-privilege application credentials or use a managed object store.

### Memory Privacy Upgrade

- Chat requests support `memory_mode=auto|normal|off`. The frontend sends `normal` or `off` explicitly; `auto` preserves legacy text-marker behavior for older clients.
- Every message persists `memory_enabled`, so private turns are excluded independently from recent context and summaries without relying only on user/assistant adjacency.
- Migration `20260710_0017` clears existing conversation summaries and resets their cursors. This is intentional: summaries created before the privacy fix may already contain opted-out content. They are rebuilt from filtered messages as conversations continue.
- The same migration persists conversation knowledge-base provenance. Ambiguous legacy multi-scope histories with no recoverable provenance fail closed instead of exposing messages after operational logs expire.

### Worker Reliability Settings

- `CELERY_TASK_MAX_RETRIES=3`: allows the initial execution plus three retries for a memory job. Keep this bounded so permanently invalid work reaches `failed` and can be inspected.
- `CELERY_TASK_RETRY_BACKOFF_SECONDS=5`: initial retry delay. Retries use exponential backoff capped by the worker.
- `MEMORY_UPDATE_JOB_LEASE_SECONDS=600`: prevents two deliveries from processing the same memory job concurrently. Production validation requires at least `(1 + MEMORY_MAX_OPERATIONS) * LLM_TIMEOUT_SECONDS`; increase it when either input changes.
- `MEMORY_UPDATE_JOB_RECOVERY_INTERVAL_SECONDS=60`: how often Beat scans memory jobs, deferred completed turns, stale conversation summaries, and external cleanup jobs. A shorter interval recovers faster but increases database polling.
- `OPERATIONAL_RETENTION_HOUR_UTC=2`: UTC hour when Beat runs the daily retention task.

Retention values use days. Setting an individual `*_RETENTION_DAYS` value to `0` disables automatic deletion for that target.

### Health Checks

- `GET /api/health` is process liveness only.
- `GET /api/ready` verifies PostgreSQL, Redis, Qdrant, MinIO, and that a Celery worker answers ping. Use this endpoint for load-balancer and backend container readiness checks.
- Beat is a separate required process and is not covered by backend readiness. Monitor it and confirm the logs show `recover-stale-memory-update-jobs`, `recover-deferred-agent-memory-updates`, `recover-stale-conversation-summaries`, `recover-stale-external-cleanup-jobs`, and `apply-operational-retention-daily`.

After deployment, verify the service state and readiness:

```powershell
docker compose -f infra/docker-compose.prod.yml --env-file .env ps
docker compose -f infra/docker-compose.prod.yml --env-file .env logs --tail=100 worker beat
Invoke-RestMethod http://localhost:5173/api/ready
```

## Required Production Settings

At minimum, set strong values for:

- `JWT_SECRET_KEY`
- `POSTGRES_PASSWORD` and the password embedded in `DATABASE_URL`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `LLM_API_KEY`
- `EMBEDDING_API_KEY`
- `BACKEND_CORS_ORIGINS`

`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` initialize only an empty PostgreSQL volume. Keep the password in `DATABASE_URL` synchronized and URL-encode reserved characters.

Use real HTTPS origins in `BACKEND_CORS_ORIGINS`. Do not use `*` or the example placeholder domain in production.

## Quality Gate

Run the project quality gate before release:

```powershell
python scripts/check_project.py
```

This validates backend tests, Python compilation, migrations, frontend build, and both development and production Docker Compose files. Compose validation uses the checked-in example templates and does not depend on a private local `.env`. GitHub Actions also builds the production frontend image, executes its Nginx parameter preflight, and runs `nginx -t`.
