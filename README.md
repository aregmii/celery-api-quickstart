# Task API

A high-performance asynchronous task processing API designed for AI inference workloads. Features credit-based billing, distributed caching, and horizontal scaling.

**Design Priority:** Speed first, reliability second.

## Components

| Service | Purpose | Port |
|---------|---------|------|
| web | FastAPI application - handles HTTP requests | 8000 |
| worker | Celery worker - processes inference tasks | - |
| postgres | PostgreSQL - durable storage (source of truth) | 5432 |
| redis | Redis - real-time cache + Celery broker | 6379 |
| flower | Celery monitoring UI | 5555 |
| pgadmin | PostgreSQL admin UI | 5050 |
| redisinsight | Redis admin UI | 5540 |

## Quick Start

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f web worker
```

---

# API Reference

## Authentication

**All endpoints except `/health` require Bearer token authentication.**

```bash
curl -H "Authorization: Bearer <api_key>" http://localhost:8000/task
```

Requests without valid Bearer token return `401 Invalid API key`.

## Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | No | Health check (for load balancer) |
| POST | `/task` | Bearer | Submit task (validates auth + credits + input) |
| GET | `/poll/{task_id}` | Bearer | Get task status/result |
| POST | `/admin/credits` | Bearer (admin) | Update user credits |

### POST /task

**Input Validation (all checked before queuing):**
1. Bearer token present and valid (auth)
2. User has available credits (credits)
3. Input parameters `a` and `b` are valid integers (type check)

```bash
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"a": 5, "b": 3}'
```

**Responses:**
- `202` - Task accepted: `{"task_id": "uuid"}`
- `401` - Invalid API key
- `402` - Insufficient credits
- `422` - Invalid input parameters

### GET /poll/{task_id}

**Optimized for speed:** Single Redis call returns task with ownership info. No separate auth lookup.

```bash
curl http://localhost:8000/poll/{task_id} \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

**Responses:**
- `200` - `{"status": "pending|in_progress|complete|failed", "result": N, "error_message": null}`
- `401` - Invalid API key
- `404` - Task not found (or not owned by user)

## Test Users

| Name | API Key | Credits |
|------|---------|---------|
| admin | `123e4567-e89b-12d3-a456-426614174000` | 1000 |
| test_user1 | `550e8400-e29b-41d4-a716-446655440000` | 500 |
| test_user2 | `c56a4180-65aa-42ec-a945-5fd21dec0538` | 250 |

---

# System Architecture

## Design Philosophy

**Speed over everything.** Redis handles all hot-path operations. Postgres is only touched when durability is required.

| Store | Role | When Used |
|-------|------|-----------|
| Redis | Shared cache (external) | Auth, credit checks, task status, polling |
| Postgres | Source of truth | Task persistence, credit finalization, recovery |

**Key architecture points:**
- Redis is **shared and external** - all API pods connect to the same Redis instance
- All requests go through the API layer (clients never touch Redis/Postgres directly)
- On Redis miss → fallback to Postgres → populate Redis (cache-aside)

---

## Redis/Postgres Consistency

**Q: What if user is in Postgres but not in Redis?**

Auth flow handles this automatically:
1. Check Redis for user → miss
2. Query Postgres → found
3. Populate Redis with user data
4. Continue with request

**Q: When does startup sync run?**

Only for **recovery** (Redis empty/crashed):
```
API starts → if user not in Redis → sync from Postgres
```
During normal operation, Redis already has data. New pods don't re-sync.

**Q: How are writes kept consistent?**

| Operation | Write Order | Rollback if fail? |
|-----------|-------------|-------------------|
| Task submit | Redis first (reserve), then Postgres | Yes, rollback Redis |
| Task complete | Postgres first, then Redis | No (Postgres is truth) |
| Admin credit update | Postgres first, then Redis | Fail request if Redis fails |

---

## Data Model

### PostgreSQL (Durable Storage)

**users**
| Column | Type | Description |
|--------|------|-------------|
| api_key | VARCHAR(36) | PRIMARY KEY |
| name | VARCHAR(255) | User name |
| total_credits | INTEGER | Assigned by admin |
| spent_credits | INTEGER | Consumed by completed tasks |

`reserved_credits` is NOT stored in Postgres. It's derived from in-flight tasks: `SUM(credits_required) FROM tasks WHERE status IN ('pending', 'in_progress')`.

**tasks**
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(36) | PRIMARY KEY |
| owner_api_key | VARCHAR(36) | FK → users.api_key |
| a, b | INTEGER | Input parameters |
| credits_required | INTEGER | Cost (always 1 for now) |
| status | VARCHAR(20) | pending / in_progress / complete / failed |
| result | INTEGER | Output (nullable) |
| error_message | TEXT | Error details (nullable) |
| created_at | TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | Last update |

### Redis (Real-time Cache)

**user:{api_key}** (Hash)
| Field | Description |
|-------|-------------|
| name | User name |
| total_credits | Assigned by admin |
| reserved_credits | Held by in-flight tasks |
| spent_credits | Consumed by completed tasks |

`available = total_credits - reserved_credits - spent_credits`

**task:{task_id}** (Hash)
| Field | Description |
|-------|-------------|
| owner_api_key | For ownership check in single call |
| a, b | Input parameters |
| credits_required | Cost |
| status | pending / in_progress / complete / failed |
| result | Output (if complete) |
| error_message | Error (if failed) |
| reservation_id | Links to reservation record |

**reservation:{reservation_id}** (Hash)
| Field | Description |
|-------|-------------|
| user_key | Which user:{api_key} |
| credits | Amount reserved |
| task_id | Which task |

**celery:task_queue** (List)
| Field | Description |
|-------|-------------|
| task_id | Task identifier |
| a, b | Input parameters |
| reservation_id | For credit tracking |

---

## Credit Model

```
available_credits = total_credits - reserved_credits - spent_credits
```

**Flow:**
1. **Task submitted** → Credits reserved (available → reserved)
2. **Task succeeds** → Credits committed (reserved → spent)
3. **Task fails** → Credits released (reserved → available)

**Users are only charged for successfully completed work.**

---

## Sequence Diagrams

### 1. Task Submission (POST /task)

Auth checks Redis first, falls back to Postgres. Credit reservation is atomic in Redis.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Redis
    participant Postgres

    Client->>API: POST /task {a, b}<br/>Authorization: Bearer <api_key>

    Note over API: STEP 1: Authenticate user

    API->>Redis: HGETALL user:{api_key}

    alt user in Redis
        Redis-->>API: {name, total_credits, spent_credits, reserved_credits}
    else user NOT in Redis
        Redis-->>API: nil
        API->>Postgres: SELECT * FROM users WHERE api_key = ?
        alt user in Postgres
            Postgres-->>API: user row
            API->>Redis: HSET user:{api_key} (populate cache)
        else user not found anywhere
            API-->>Client: 401 Invalid API key
        end
    end

    Note over API: STEP 2: Reserve credits (atomic Lua script)

    API->>Redis: Reserve 1 credit for user:{api_key}

    Note over Redis: Lua script checks:<br/>if available < 1 → 402<br/>else reserved += 1, create reservation

    alt insufficient credits
        Redis-->>API: INSUFFICIENT_CREDITS
        API-->>Client: 402 Insufficient credits
    else success
        Redis-->>API: reservation_id
    end

    Note over API: STEP 3: Create task in Redis

    API->>Redis: HSET task:{task_id}

    Note over API: STEP 4: Persist to Postgres

    API->>Postgres: INSERT INTO tasks
    Postgres-->>API: OK

    Note over API: If Postgres fails → rollback Redis → 500

    Note over API: STEP 5: Queue to Celery

    API->>Redis: LPUSH celery:task_queue

    API-->>Client: 202 {task_id}
```

### 2. Poll for Result (GET /poll/{task_id})

**Optimized:** Single Redis call. Task hash contains `owner_api_key` for ownership check.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Redis
    participant Postgres

    Client->>API: GET /poll/{task_id}<br/>Authorization: Bearer <api_key>

    Note over API: Single Redis call for task + ownership

    API->>Redis: HGETALL task:{task_id}

    alt found in Redis
        Redis-->>API: {owner_api_key, status, result, ...}

        alt owner_api_key != request api_key
            API-->>Client: 404 Task not found
        else owned by user
            API-->>Client: 200 {status, result, error_message}
        end
    else not in Redis (old task or cache miss)
        API->>Postgres: SELECT * FROM tasks WHERE id = ?

        alt not found or wrong owner
            API-->>Client: 404 Task not found
        else found
            API-->>Client: 200 {status, result, error_message}
        end
    end
```

### 3. Worker Processing

**Postgres-first writes** for durability. Worker signals ready only after model loaded.

```mermaid
sequenceDiagram
    participant Redis
    participant Worker
    participant Postgres

    Note over Worker: Worker ready (model loaded)

    Worker->>Redis: BRPOP celery:task_queue (blocking)
    Redis-->>Worker: {task_id, a, b, reservation_id}

    Note over Worker: Idempotency check

    Worker->>Postgres: SELECT status FROM tasks WHERE id = ?

    alt already complete/failed
        Note over Worker: Skip processing, fix Redis state
        Worker->>Redis: Update task:{task_id} to match Postgres
    else pending/in_progress
        Note over Worker: Process task

        Worker->>Postgres: UPDATE tasks SET status='in_progress'
        Worker->>Redis: HSET task:{task_id} status=in_progress

        Worker->>Worker: Compute result (a + b)

        alt success
            Note over Worker: Postgres FIRST (durable)
            Worker->>Postgres: UPDATE tasks SET status='complete', result=N
            Worker->>Postgres: UPDATE users SET spent_credits += 1

            Note over Worker: Then Redis (fast reads)
            Worker->>Redis: Commit credits (spent += 1, reserved -= 1)
            Worker->>Redis: Delete reservation:{id}
            Worker->>Redis: HSET task:{task_id} status=complete, result=N

        else failure (final)
            Worker->>Postgres: UPDATE tasks SET status='failed', error_message=...
            Worker->>Redis: Release credits (reserved -= 1)
            Worker->>Redis: Delete reservation:{id}
            Worker->>Redis: HSET task:{task_id} status=failed
        end
    end
```

### 4. Admin Update Credits (POST /admin/credits)

**Both stores updated synchronously.** If Redis update fails, request fails (prevents race condition).

```mermaid
sequenceDiagram
    participant Admin
    participant API
    participant Redis
    participant Postgres

    Admin->>API: POST /admin/credits<br/>{user_api_key, credits: 1000}

    API->>Redis: HGETALL user:{admin_api_key}

    alt not admin
        API-->>Admin: 403 Admin access required
    end

    Note over API: Update BOTH stores (sync)

    API->>Postgres: UPDATE users SET total_credits = 1000
    Postgres-->>API: OK

    API->>Redis: HSET user:{target} total_credits = 1000

    alt Redis fails
        Note over API: Rollback Postgres? Or accept inconsistency?<br/>Current: Fail request, log for manual fix
        API-->>Admin: 500 Credit update failed
    else success
        API-->>Admin: 200 Credits updated
    end
```

---

# Worker Configuration

## AI Workload Considerations

AI inference tasks have unique characteristics:

| Characteristic | Challenge | Solution |
|---------------|-----------|----------|
| **Cold start** | Model loading takes 1-20 min | Warm pool, pre-baked images |
| **Long-running** | Some tasks take minutes | No hard timeouts, soft limits |
| **Short-running** | Some tasks take milliseconds | Batch processing, keep-warm |
| **Memory intensive** | GPU memory fragmentation | Worker recycling |
| **Variable duration** | p50 = 2s, p99 = 60s | Priority queues |

## Worker Ready Signal

Workers only accept tasks after heavy initialization completes:

```python
@celery_app.task(bind=True)
def compute_task(self, task_id, a, b, reservation_id):
    # This task only runs after worker signals ready
    ...

# In worker startup:
@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    # Model loading happens here
    load_model_into_gpu()
    logger.info("Worker ready, model loaded")
```

## Celery Configuration

```python
celery_app.conf.update(
    # One task at a time (no prefetching)
    worker_prefetch_multiplier=1,

    # Acknowledge after completion (reliability)
    task_acks_late=True,

    # Requeue if worker dies
    task_reject_on_worker_lost=True,

    # Recycle workers to prevent memory leaks
    worker_max_tasks_per_child=50,

    # Soft timeout (allows graceful shutdown)
    task_soft_time_limit=300,

    # Hard timeout (kills stuck tasks)
    task_time_limit=330,

    # Retry with exponential backoff
    task_default_retry_delay=1,
    task_max_retries=3,
)
```

## Task Failure Handling

| Failure Type | Behavior |
|--------------|----------|
| Exception in task | Retry with backoff (up to 3 times) |
| Max retries exceeded | Mark failed, release credits, log error |
| Worker crash mid-task | Task requeued (acks_late=True), idempotency check prevents duplicate work |
| Redis unavailable | Postgres write succeeds, Redis update logged as warning, self-heals on sync |

## Celery Disconnect Handling

```python
@celery_app.task(bind=True)
def compute_task(self, task_id, ...):
    try:
        # ... task logic ...
    except redis.ConnectionError:
        # Redis down - Postgres is source of truth
        logger.warning(f"Redis unavailable, Postgres updated")
        # Task completes, Redis will sync on recovery
```

---

# Logging & Observability

## Structured Logging

All logs are JSON for aggregation (Datadog, Splunk, ELK):

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "info",
  "message": "Task completed",
  "request_id": "abc-123",
  "task_id": "def-456",
  "user": "test_user1",
  "duration_ms": 2150,
  "result": 8
}
```

## Distributed Tracing

`X-Request-ID` header propagates through the system:

```
Client → API (generates request_id)
         → Redis (logged)
         → Postgres (logged)
         → Celery (passed in task args)
              → Worker (logged with same request_id)
```

## Key Metrics

| Metric | Type | Alert Threshold |
|--------|------|-----------------|
| `task_queue_depth` | Gauge | > 1000 for 5 min |
| `task_duration_seconds` | Histogram | p99 > 60s |
| `credit_operations_total` | Counter | error_rate > 5% |
| `redis_connection_errors` | Counter | > 10/min |
| `worker_utilization` | Gauge | < 20% (over-provisioned) |

---

# Kubernetes Migration

## Architecture on K8s

```
                    ┌─────────────────┐
                    │  Ingress / LB   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │  API Pod  │  │  API Pod  │  │  API Pod  │
        │ (FastAPI) │  │ (FastAPI) │  │ (FastAPI) │
        └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │  Redis    │  │ Postgres  │  │  Worker   │
        │ (Primary) │  │ (Primary) │  │   Pods    │
        └───────────┘  └───────────┘  └───────────┘
```

## Minikube Setup

```bash
# Start minikube with enough resources
minikube start --cpus=4 --memory=8192

# Enable ingress
minikube addons enable ingress

# Deploy
kubectl apply -f k8s/
```

## Autoscaling (HPA)

**API Pods:** Scale on CPU (standard web traffic)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

**Worker Pods:** Scale on queue depth (custom metric)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: worker-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: worker
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: External
    external:
      metric:
        name: celery_queue_depth
      target:
        type: AverageValue
        averageValue: 10  # Scale up when > 10 tasks per worker
```

## Network Bottlenecks

| Bottleneck | Symptom | Solution |
|------------|---------|----------|
| Redis connections | Connection timeouts | Connection pooling per pod, Redis Cluster |
| Postgres connections | "too many connections" | PgBouncer sidecar or separate deployment |
| Inter-pod latency | Slow Redis calls | Same-node affinity for Redis-heavy pods |
| Ingress throughput | 503 errors | Multiple ingress replicas, rate limiting |

### Connection Pooling

Each API pod maintains its own connection pool:

```python
# Per-pod Redis pool
redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    max_connections=20,  # Per pod
    socket_timeout=1.0,
    socket_connect_timeout=1.0,
)

# Per-pod Postgres pool (via asyncpg)
db_pool = await asyncpg.create_pool(
    dsn=DATABASE_URL,
    min_size=5,
    max_size=20,  # Per pod
)
```

**Why per-pod?** Sharing pools across pods requires external poolers (PgBouncer, Redis Cluster). Per-pod pools are simpler and sufficient for moderate scale.

---

# Multi-Region Deployment

## High-Level Architecture

```
                US-EAST                                    EU-WEST
    ┌─────────────────────────────┐          ┌─────────────────────────────┐
    │                             │          │                             │
    │  ┌─────────┐  ┌─────────┐   │          │  ┌─────────┐  ┌─────────┐   │
    │  │   API   │  │   API   │   │          │  │   API   │  │   API   │   │
    │  └────┬────┘  └────┬────┘   │          │  └────┬────┘  └────┬────┘   │
    │       │            │        │          │       │            │        │
    │  ┌────▼────────────▼────┐   │          │  ┌────▼────────────▼────┐   │
    │  │     Redis (Local)    │   │          │  │     Redis (Local)    │   │
    │  └──────────────────────┘   │          │  └──────────────────────┘   │
    │                             │          │                             │
    │  ┌──────────────────────┐   │          │  ┌──────────────────────┐   │
    │  │  Workers (GPU)       │   │          │  │  Workers (GPU)       │   │
    │  └──────────────────────┘   │          │  └──────────────────────┘   │
    │                             │          │                             │
    └──────────────┬──────────────┘          └──────────────┬──────────────┘
                   │                                        │
                   │         ┌──────────────────┐           │
                   └─────────┤ Postgres Primary ├───────────┘
                             │    (US-EAST)     │
                             └────────┬─────────┘
                                      │
                             ┌────────▼─────────┐
                             │ Postgres Replica │
                             │    (EU-WEST)     │
                             └──────────────────┘
```

## Database Strategy

**Single Primary + Read Replicas** (simpler than multi-primary)

| Operation | Where | Latency Impact |
|-----------|-------|----------------|
| Reads (poll) | Local replica | ~5ms |
| Writes (task submit) | Cross-region to primary | +100-150ms |
| Credit updates | Cross-region to primary | +100-150ms |

**Why not multi-primary?** Conflict resolution is complex. Credit operations need strong consistency. Cross-region write latency is acceptable for async task submission.

## Redis Strategy

**Local Redis per region.** Each region's Redis is independent.

| Challenge | Solution |
|-----------|----------|
| User submits in US, polls in EU | Embed region hint in task_id (e.g., `us-east:uuid`) |
| Redis state divergence | Each region rebuilds from Postgres on startup |
| Cache invalidation | Admin credit updates go to all regions (fan-out) |

## Latency Tradeoffs

| Scenario | US-EAST User | EU-WEST User |
|----------|--------------|--------------|
| Submit task | ~50ms (local) | ~150ms (cross-region write) |
| Poll result | ~10ms (local Redis) | ~10ms (local Redis) |
| Admin update | ~50ms | ~150ms |

**Key insight:** Task submission latency matters less for async workloads. Users wait for results anyway.

## Failover

| Component | Strategy | RTO |
|-----------|----------|-----|
| Postgres | Streaming replication, automatic failover | < 30s |
| Redis | Accept ephemeral, rebuild from Postgres | < 60s |
| Region | DNS failover to healthy region | < 5 min |

---

# Implementation Checklist

## Core (Must Have)
- [ ] Credit logic: reserve → commit/release
- [ ] Distributed caching: user + task in Redis
- [ ] Input validation: auth + credits + types before queue
- [ ] Rollback on failure at any step

## Worker (Must Have)
- [ ] Redis task backend
- [ ] Failure handling with retry
- [ ] Celery disconnect handling
- [ ] Ready signal after model load
- [ ] One task at a time (prefetch=1)

## Observability (Should Have)
- [ ] Structured JSON logging
- [ ] Distributed tracing (X-Request-ID)
- [ ] Key metrics exposed

## Kubernetes (Discussion + Demo if time)
- [ ] Deployment manifests
- [ ] HPA for API (CPU-based)
- [ ] HPA for Workers (queue depth)
- [ ] Network/connection pooling strategy

## Multi-Region (Discussion Only)
- [ ] Architecture diagram
- [ ] Database replication strategy
- [ ] Redis per region approach
- [ ] Latency tradeoffs understood

---

# Project Structure

```
.
├── compose.yaml
├── k8s/                      # Kubernetes manifests
│   ├── api-deployment.yaml
│   ├── worker-deployment.yaml
│   ├── redis-deployment.yaml
│   ├── postgres-deployment.yaml
│   └── hpa.yaml
├── api/
│   ├── Dockerfile
│   ├── alembic/              # Database migrations
│   ├── main.py               # FastAPI app + startup sync
│   ├── config.py
│   ├── logger.py             # Structlog setup
│   ├── celery_app.py
│   ├── models/
│   ├── routes/
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── task_service.py
│   │   └── redis_service.py  # NEW: atomic Redis ops
│   ├── repositories/
│   └── worker/
│       └── tasks.py
└── ...
```

---

# Testing

```bash
# Unit tests
docker compose exec web pytest tests/ -v

# Integration tests
docker compose exec web python scripts/test_integration.py

# Load test (queue depth demo)
docker compose exec web python scripts/load_test.py --tasks 100
```

---

# Monitoring

| Service | URL | Purpose |
|---------|-----|---------|
| Flower | http://localhost:5555 | Celery task monitoring |
| pgAdmin | http://localhost:5050 | PostgreSQL admin |
| RedisInsight | http://localhost:5540 | Redis admin |
