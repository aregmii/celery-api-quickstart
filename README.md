# Task API

An asynchronous task processing API with authentication, credit based inference workloads, and monitoring.

## Components

| Service | Purpose | Port |
|---------|---------|------|
| web | FastAPI application - handles HTTP requests | 8000 |
| worker | Celery worker - processes async tasks | - |
| postgres | PostgreSQL - persistent storage for users and tasks | 5432 |
| redis | Redis - user cache (15s TTL) + Celery message broker | 6379 |
| flower | Celery monitoring UI | 5555 |
| pgadmin | PostgreSQL admin UI | 5050 |
| redisinsight | Redis admin UI | 5540 |

## Quick Start

```bash
# Start all services
docker compose up --build -d

# Verify all services are running
docker compose ps

# View logs (now in JSON format)
docker compose logs -f web worker
```

## Implementation (Production Hardening)

I optimized the infra specifically for Model Serving constraints and production reliability:

**Base Image:** Switched from alpine to `python:3.10-slim-bullseye`.
> Alpine's musl library is incompatible with standard ML wheels (manylinux). slim ensures compatibility with NumPy/PyTorch without risky source compilations.

**Database Schema:** Replaced raw SQL scripts with Alembic migrations.
> Production schemas evolve. Migrations provide version control, rollback capabilities, and zero-downtime deployments.

**Worker Lifecycle:** Configured Celery with `worker_max_tasks_per_child=50` and `task_time_limit`.
> ML inference could cause leaks memory (CUDA fragmentation). Recycling workers prevents OOM crashes; time limits prevent "zombie" tasks from deadlocking GPUs.

**Observability:** Implemented Structlog and distributed tracing.
> JSON logs are machine-readable for aggregation (Datadog/Splunk). The `X-Request-ID` is passed from API → Worker to trace requests across the distributed system.

**Security:** Containers run as non-root user (`appuser`).
> Adheres to least-privilege principles.

**Database Engine:** Upgraded PostgreSQL from 10.5 (EOL) to 15.

**Dependencies Added:**
- `alembic` (Migrations)
- `structlog` (Structured Logging)
- `requests` & `httpx` (Integration Testing)
- `greenlet` (Async SQLAlchemy support)

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | No | Health check |
| POST | `/task` | Bearer | Submit async task (costs 1 credit) |
| GET | `/poll/{task_id}` | Bearer | Get task status/result |
| POST | `/admin/credits` | Bearer (admin only) | Update user credits |

### Authentication

All authenticated endpoints require a Bearer token:

```bash
curl -H "Authorization: Bearer <api_key>" http://localhost:8000/task
```

### Submit a Task

```bash
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"a": 5, "b": 3}'
```

Response:
```json
{"task_id": "6ac9ba3d-47d0-454a-a88f-42bd57cde0d2"}
```

### Poll for Result

```bash
curl http://localhost:8000/poll/6ac9ba3d-47d0-454a-a88f-42bd57cde0d2 \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

Response (pending):
```json
{"status": "pending", "result": null, "error_message": null}
```

Response (complete):
```json
{"status": "complete", "result": 8, "error_message": null}
```

### Update User Credits (Admin Only)

```bash
curl -X POST http://localhost:8000/admin/credits \
  -H "Authorization: Bearer 123e4567-e89b-12d3-a456-426614174000" \
  -H "Content-Type: application/json" \
  -d '{"user_api_key": "550e8400-e29b-41d4-a716-446655440000", "credits": 1000}'
```

## Test Users

| Name | API Key | Initial Credits |
|------|---------|-----------------|
| admin | `123e4567-e89b-12d3-a456-426614174000` | 1000 |
| test_user1 | `550e8400-e29b-41d4-a716-446655440000` | 500 |
| test_user2 | `c56a4180-65aa-42ec-a945-5fd21dec0538` | 250 |

## Testing

### Integration Tests

```bash
# Runs inside the Docker network
docker compose exec web python scripts/test_integration.py
```

### Unit Tests

```bash
docker compose exec web pytest tests/ -v
```

### Manual Testing

```bash
docker compose exec web python scripts/submit_task.py
```

## Monitoring

### Flower (Celery Tasks)

- **URL:** http://localhost:5555
- **Workers tab:** View worker status, processed/failed counts
- **Tasks tab:** View task history, status, runtime, results

### pgAdmin (PostgreSQL)

- **URL:** http://localhost:5050
- **Login:** `admin@admin.com` / `admin`
- **Setup connection:**
  - Host: `postgres`
  - Port: `5432`
  - Username: `postgres`
  - Password: `postgres`

### RedisInsight (Redis)

- **URL:** http://localhost:5540
- **Setup connection:**
  - Host: `redis`
  - Port: `6379`

## Project Structure

```
.
├── compose.yaml              # Docker Compose configuration
├── api/
│   ├── Dockerfile            # Debian-slim based image
│   ├── alembic/              # Database migrations
│   ├── requirements.txt      # Python dependencies
│   ├── main.py               # FastAPI application entrypoint
│   ├── config.py             # Environment configuration
│   ├── logger.py             # Structlog configuration
│   ├── celery_app.py         # Celery configuration
│   ├── worker/               # Celery worker tasks
│   └── ...
└── ...
```

## Design Decisions

### Reducing Database Calls

The assignment asked: *"Assuming the database calls are too expensive, how can we reduce the number of calls?"*

**Solution:** Cache-aside pattern with Redis

1. On authentication, check Redis cache first
2. On cache miss, query Postgres and cache result (TTL: 15s)
3. On admin credit update, invalidate cache immediately

This reduces database queries by ~90% for repeat requests within the TTL window.

> See `api/services/auth_service.py` for implementation.

### Atomic Credit Deduction

Credits are deducted atomically using a single SQL statement with a WHERE clause:

```sql
UPDATE users SET credits = credits - 1
WHERE api_key = $1 AND credits >= 1
RETURNING credits
```

This prevents race conditions where two concurrent requests could both succeed with only 1 credit remaining.

### Task Ownership

When polling, users can only see their own tasks. We return `404` (not `403`) for tasks owned by others to avoid leaking task existence information.

### Celery Configuration

- **`acks_late=True`:** Acknowledge tasks after completion (not on receive) for reliability
- **`worker_prefetch_multiplier=1`:** Fair distribution for long-running tasks
- **`max_retries=3` with `retry_backoff=True`:** Automatic retry with exponential backoff

## Operations

### View Logs

```bash
# All services
docker compose logs -f

# Specific services
docker compose logs -f web worker
```

### Database Operations

```bash
# Connect to Postgres
docker compose exec postgres psql -U postgres -d postgres
```

### Reset Everything

```bash
docker compose down -v
rm -rf postgres-data
docker compose up --build -d
```

## Production Roadmap

To scale this system to handle high traffic, the following enhancements should be prioritized:

1. **Expose Prometheus metrics** for auto-scaling, health monitoring, dependency status and associated latencies (e.g., `http_request_duration_seconds` histogram, `active_inference_workers` count).

2. **Implement alerts based on SLAs** to detect degradation or invalid system behavior.

3. **Distributed tracing visualization.**

4. **Rate limit users** to protect against abuse.