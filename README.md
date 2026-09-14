# Celery API Quickstart

A prototype for accepting work over HTTP, processing it in background workers, and returning results through polling. It combines FastAPI, Celery, Redis, and PostgreSQL to demonstrate authenticated task submission, ownership checks, and a credit reservation workflow.

The included workload waits two seconds and adds two integers, keeping the focus on task lifecycle and service coordination. The credit system is a usage-accounting demonstration; it does not process payments.

## Overview

- **Submit and track work:** receive a task ID immediately, then poll for completion.
- **Control access:** authenticate with seeded API keys and restrict results to the submitting user.
- **Track usage:** reserve one credit per task and record spent credits on success.
- **Inspect processing:** use Celery Flower and application logs to follow worker activity.

This repository is a development prototype. The unit tests need updates for the current credit model, and the missing Alembic configuration requires the setup step below. See [current limitations](#current-limitations) before extending the design.

## Architecture

```text
Client -> FastAPI -> PostgreSQL (users and task records)
             |
             v
           Redis (user/task cache, reservations, Celery broker/backend)
             |
             v
        Celery workers -> PostgreSQL and Redis (results and credits)
```

[TaskService](api/services/task_service.py) reserves credits and creates the task cache in one Redis Lua operation, persists the task in PostgreSQL, and dispatches it to Celery. The [worker](api/worker/tasks.py) computes the result and updates task status and credits. Polling authenticates the caller, checks task ownership, and reads Redis with PostgreSQL fallback.

The [Compose configuration](compose.yaml) defines these five services:

| Service | Purpose | Local access |
| --- | --- | --- |
| `web` | FastAPI application | [API docs](http://localhost:8000/docs), [health](http://localhost:8000/health) |
| `worker` | Celery worker with four processes | Container logs |
| `postgres` | PostgreSQL 15 | `localhost:5432` |
| `redis` | Cache, reservations, broker, and result backend | Internal Compose network, port `6379` |
| `flower` | Celery monitoring | [Flower](http://localhost:5555) |

## Getting started

### Prerequisites

- Git, Docker Engine or Docker Desktop, and Docker Compose v2.
- Available host ports `8000`, `5432`, and `5555`.
- Network access for the first container build and image downloads.

The application image uses Python 3.10. A separate host Python installation is unnecessary for the container workflow.

### Set up and start

```bash
git clone https://github.com/aregmii/celery-api-quickstart.git
cd celery-api-quickstart
```

The repository includes migrations but does not track `api/alembic.ini`; `.gitignore` excludes `*.ini`. Create this local configuration **before building** so the Dockerfile copies it into the application image. These instructions assume a fresh checkout.

```bash
cat > api/alembic.ini <<'INI'
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root

[handlers]
keys =

[formatters]
keys =

[logger_root]
level = WARNING
handlers =
INI

docker compose config --quiet
docker compose build
docker compose run --rm web alembic upgrade head
docker compose up -d
docker compose ps
```

Running migrations before starting the worker avoids a first-start race against table creation. The `web` service also runs `alembic upgrade head` on startup; rerunning applied migrations does not reseed the database.

```bash
curl http://localhost:8000/health
docker compose logs -f web worker
```

The health endpoint returns `{"status":"healthy"}`. It reports that the API responds; it does not test current database, Redis, or worker health.

Stop the stack with `docker compose down`. PostgreSQL data remains in the local `postgres-data/` directory. Redis has no configured persistent volume.

## API usage

The application routes are defined in [api/routes](api/routes), with request and response models in [api/models](api/models).

| Method | Route | Access | Result |
| --- | --- | --- | --- |
| `GET` | `/health` | Public | API health response |
| `POST` | `/task` | Bearer API key | `202` with a task ID |
| `GET` | `/poll/{task_id}` | Bearer API key; task owner | Status, result, and error message |
| `POST` | `/admin/credits` | Bearer API key; user named `admin` | Replace a user's total credit allocation |

Interactive documentation is available at [Swagger UI](http://localhost:8000/docs). The seeded credentials below are public development fixtures, and the Compose stack uses default PostgreSQL credentials with unauthenticated Flower access. Use the stack in a trusted local environment.

| User | API key | Initial total credits |
| --- | --- | --- |
| `admin` | `123e4567-e89b-12d3-a456-426614174000` | 1,000 |
| `test_user1` | `550e8400-e29b-41d4-a716-446655440000` | 500 |
| `test_user2` | `c56a4180-65aa-42ec-a945-5fd21dec0538` | 250 |

Submit an addition task:

```bash
curl -X POST http://localhost:8000/task \
  -H 'Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000' \
  -H 'Content-Type: application/json' \
  -d '{"a":5,"b":3}'
```

The response contains `{"task_id":"<generated-id>"}`. Replace `TASK_ID` below with that value:

```bash
curl http://localhost:8000/poll/TASK_ID \
  -H 'Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000'
```

Statuses are `pending`, `in_progress`, `complete`, or `failed`. A successful result is `{"status":"complete","result":8,"error_message":null}`. An unknown API key returns `401`, insufficient credits return `402`, an unavailable or unowned task returns `404`, and invalid request data returns `422`.

To replace `test_user2`'s total allocation with 1,000 credits:

```bash
curl -X POST http://localhost:8000/admin/credits \
  -H 'Authorization: Bearer 123e4567-e89b-12d3-a456-426614174000' \
  -H 'Content-Type: application/json' \
  -d '{"user_api_key":"c56a4180-65aa-42ec-a945-5fd21dec0538","credits":1000}'
```

Available credits equal `total_credits - reserved_credits - spent_credits`. Updating the total does not reset prior spending or reservations. Non-admin users receive `403` on this route.

## Configuration

[api/config.py](api/config.py) reads component settings from environment variables. Set them on the relevant services in `compose.yaml` when changing the deployment.

| Variables | Defaults |
| --- | --- |
| `DATABASE_HOST`, `DATABASE_PORT` | `postgres`, `5432` |
| `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME` | `postgres` for each |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | `redis`, `6379`, `0` |
| `LOG_LEVEL` | `INFO` |

`DATABASE_URL` and `REDIS_URL` appear in Compose, but the application builds its connection URLs from the component settings above; changing those URL environment variables alone does not change its connections. `CACHE_TTL_SECONDS` is declared but is not applied to the user and task hashes.

[api/celery_app.py](api/celery_app.py) configures late acknowledgments, requeueing after worker loss, a prefetch multiplier of one, process recycling after 50 tasks, and soft/hard limits of 300/330 seconds. Compose starts four worker processes. The task explicitly retries exceptions up to three times with a configured one-second default delay.

## Testing

Once the image is built, run the existing unit tests without starting the application:

```bash
docker compose run --rm --no-deps web python -m pytest tests/ -v
```

**Known test drift:** fixtures still pass `credits` where the current `User` model requires `total_credits`; several tests also reference removed methods and an outdated `TaskService` constructor. The suite needs revision before it can serve as a passing regression check.

With the stack running, the included client exercises one task, and the integration script checks API behavior:

```bash
docker compose exec web python scripts/submit_task.py
docker compose exec web python scripts/test_integration.py
```

The integration script submits tasks, consumes development credits, and changes `test_user2`'s total allocation to 999. Its missing-auth expectation depends on the installed FastAPI version; dependencies in [requirements.txt](api/requirements.txt) are unpinned. Use a disposable development database for these checks.

## Current limitations

- **Recovery:** [startup synchronization](api/main.py) repopulates missing users, task hashes, and reservations. It does not re-enqueue tasks after broker data loss or reconcile all partial failures.
- **Consistency:** task completion and credit spending are separate database operations. PostgreSQL, Redis, and queue writes do not share a transaction. The completion check is not an atomic task claim and does not guarantee exactly-once execution or billing.
- **Submission failures:** an enqueue failure after database insertion can leave a pending database record while Redis reservations are rolled back.
- **Operations:** no Kubernetes manifests, autoscaling, multi-region deployment, metrics exporter, pgAdmin, or RedisInsight service is included. API request IDs are not passed to Celery tasks.
- **Validation:** no performance or recovery benchmarks are included. Dependencies are unpinned, and the unit suite needs maintenance.

## Repository structure

```text
compose.yaml                 Local service definitions
api/
  Dockerfile                 Python application image
  main.py                    API lifecycle and startup synchronization
  config.py                  Environment settings
  celery_app.py              Worker and broker configuration
  alembic/                   Database migrations and seed users
  models/                    Request, response, and domain models
  routes/                    Health, task, polling, and admin endpoints
  services/                  Authentication, task flow, and Redis operations
  repositories/              PostgreSQL queries
  worker/tasks.py            Background addition task and credit updates
  tests/                     Unit tests requiring maintenance
  scripts/                   Example client and integration checks
utils/hit.sh                 Additional request helper
DEMO_GUIDE.md                 Earlier demo notes; some assumptions are outdated
```

## License

No license file is included in this repository. Reuse terms have not been specified.
