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

## Sequence Diagrams

### 1. Task Submission (POST /task)

Auth checks Redis first, falls back to Postgres. Credit reservation is atomic via Lua script.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Redis
    participant Postgres

    Client->>API: POST /task {a, b} with Bearer token

    Note over API: STEP 1: Authenticate user

    API->>Redis: Get user by api_key

    alt user found in Redis
        Redis-->>API: User data (name, credits, etc.)
    else user NOT in Redis (cache miss)
        Redis-->>API: Not found
        API->>Postgres: Query user by api_key
        alt user exists in Postgres
            Postgres-->>API: User row
            API->>Redis: Cache user data
        else user not found
            API-->>Client: 401 Invalid API key
        end
    end

    Note over API: STEP 2: Reserve credits (atomic Lua script)

    API->>Redis: Reserve 1 credit (Lua script)

    Note over Redis: Lua script atomically:<br/>1. Check available >= 1<br/>2. Increment reserved_credits<br/>3. Create reservation record

    alt insufficient credits
        Redis-->>API: Error: insufficient credits
        API-->>Client: 402 Insufficient credits
    else success
        Redis-->>API: reservation_id
    end

    Note over API: STEP 3: Create task in Redis

    API->>Redis: Store task data (status=pending)

    Note over API: STEP 4: Persist to Postgres

    API->>Postgres: Insert task row
    Postgres-->>API: OK

    Note over API: If Postgres fails → rollback Redis → 500

    Note over API: STEP 5: Queue to Celery

    API->>Redis: Push task to Celery queue

    API-->>Client: 202 {task_id}
```

### 2. Poll for Result (GET /poll/{task_id})

**Optimized:** Single Redis call returns task with owner for ownership check.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Redis
    participant Postgres

    Client->>API: GET /poll/{task_id} with Bearer token

    API->>Redis: Get task by task_id

    alt task found in Redis
        Redis-->>API: Task data (owner, status, result, etc.)

        alt owner doesn't match request api_key
            API-->>Client: 404 Task not found
        else owner matches
            API-->>Client: 200 {status, result, error_message}
        end
    else task not in Redis (cache miss)
        API->>Postgres: Query task by id

        alt not found or wrong owner
            API-->>Client: 404 Task not found
        else found and owned
            API-->>Client: 200 {status, result, error_message}
        end
    end
```

### 3. Worker Processing

**Postgres-first writes** for durability. Worker processes one task at a time.

```mermaid
sequenceDiagram
    participant Redis
    participant Worker
    participant Postgres

    Note over Worker: Worker ready (model loaded)

    Worker->>Redis: Wait for task from queue (blocking)
    Redis-->>Worker: Task data (task_id, a, b, reservation_id)

    Note over Worker: Idempotency check

    Worker->>Postgres: Get task status

    alt already complete or failed
        Note over Worker: Skip processing
        Worker->>Redis: Sync task status to Redis
    else pending or in_progress
        Note over Worker: Process task

        Worker->>Postgres: Set status = in_progress
        Worker->>Redis: Set status = in_progress

        Worker->>Worker: Compute result (a + b)

        alt success
            Note over Worker: Postgres FIRST (durable)
            Worker->>Postgres: Set status = complete, result = N
            Worker->>Postgres: Increment user spent_credits

            Note over Worker: Then Redis (fast reads)
            Worker->>Redis: Commit credits (reserved → spent)
            Worker->>Redis: Delete reservation
            Worker->>Redis: Set status = complete, result = N

        else failure (after max retries)
            Worker->>Postgres: Set status = failed, error_message
            Worker->>Redis: Rollback credits (release reserved)
            Worker->>Redis: Delete reservation
            Worker->>Redis: Set status = failed
        end
    end
```

### 4. Admin Update Credits (POST /admin/credits)

**Both stores updated synchronously.** If Redis fails after Postgres, request fails.

```mermaid
sequenceDiagram
    participant Admin
    participant API
    participant Redis
    participant Postgres

    Admin->>API: POST /admin/credits {user_api_key, credits: 1000}

    API->>Redis: Get admin user data

    alt not admin user
        API-->>Admin: 403 Admin access required
    end

    Note over API: Update BOTH stores

    API->>Postgres: Update user total_credits = 1000
    Postgres-->>API: OK

    API->>Redis: Update user total_credits = 1000

    alt Redis update fails
        Note over API: Postgres updated but Redis failed<br/>Fail request to signal inconsistency
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
│   │   └── redis_service.py  # Atomic Redis ops
│   ├── repositories/
│   └── worker/
│       └── tasks.py
└── ...
```

---

# Monitoring

| Service | URL | Purpose |
|---------|-----|---------|
| API | http://localhost:8000 | FastAPI application |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Flower | http://localhost:5555 | Celery task monitoring |
| pgAdmin | http://localhost:5050 | PostgreSQL admin |
| RedisInsight | http://localhost:5540 | Redis admin |

---

# Testing & Verification

Step-by-step guide to test the system and verify each operation through the admin UIs.

## 1. Start the Stack

```bash
# Build and start all services
docker compose up --build -d

# Verify all services are healthy
docker compose ps

# Watch logs (in a separate terminal)
docker compose logs -f web worker
```

## 2. Verify Database Setup

**Check users and tasks tables in pgAdmin:**

1. Open http://localhost:5050
2. Login: `admin@admin.com` / `admin`
3. Add server: Host=`postgres`, Port=`5432`, User=`postgres`, Password=`postgres`
4. Navigate to: Servers → postgres → Databases → postgres → Schemas → public → Tables
5. Right-click `users` → View/Edit Data → All Rows

You should see the seeded test users (admin, test_user1, test_user2).

## 3. Check Redis State

**View user and task hashes in RedisInsight:**

1. Open http://localhost:5540
2. Add database: Host=`localhost`, Port=`6379`
3. Click "Browser" in the left sidebar
4. Search for `user:*` to see cached user data

Initially empty until first API request populates the cache.

## 4. Submit a Task

```bash
# Submit task as test_user1
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"a": 5, "b": 3}'
```

Response: `{"task_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}`

**Verify in RedisInsight:**
- Search `user:550e8400*` → see `reserved_credits` increased by 1
- Search `task:*` → see new task hash with status=`pending`
- Search `reservation:*` → see credit reservation record

**Verify in pgAdmin:**
- Query: `SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1;`
- See the new task with status=`pending`

## 5. Watch Worker Process the Task

**In Flower (http://localhost:5555):**

1. Click "Tasks" in the top nav
2. See `worker.tasks.compute_task` appear
3. Watch status change: PENDING → STARTED → SUCCESS

**In terminal (docker compose logs):**
```
worker  | Task task_id=abc123... starting
worker  | Task task_id=abc123... completed with result=8
```

## 6. Poll for Result

```bash
# Replace with your actual task_id
curl http://localhost:8000/poll/{task_id} \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

Response: `{"status": "complete", "result": 8, "error_message": null}`

**Verify in RedisInsight:**
- Search `task:{task_id}` → status=`complete`, result=`8`
- Search `user:550e8400*` → `reserved_credits` decreased, `spent_credits` increased
- Search `reservation:*` → reservation record deleted

**Verify in pgAdmin:**
- Query: `SELECT * FROM tasks WHERE id = '{task_id}';`
- Status=`complete`, result=`8`
- Query: `SELECT * FROM users WHERE api_key = '550e8400-e29b-41d4-a716-446655440000';`
- `spent_credits` increased by 1

## 7. Test Insufficient Credits

```bash
# Use test_user2 who has limited credits (250)
# Submit many tasks to exhaust credits, then try again
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer c56a4180-65aa-42ec-a945-5fd21dec0538" \
  -H "Content-Type: application/json" \
  -d '{"a": 1, "b": 1}'
```

When credits exhausted: `402 {"detail": "Insufficient credits"}`

## 8. Test Invalid API Key

```bash
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer invalid-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"a": 1, "b": 1}'
```

Response: `401 {"detail": "Invalid API key"}`

## 9. Load Test (Queue Depth Demo)

```bash
# Submit 20 tasks rapidly to see queue build up
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/task \
    -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
    -H "Content-Type: application/json" \
    -d "{\"a\": $i, \"b\": $i}" &
done
wait
```

**Watch in Flower:**
- See tasks queuing up in "Active" and "Reserved" columns
- Monitor worker processing rate

**Watch in RedisInsight:**
- Search `task:*` → see multiple tasks with various statuses
- Check celery queue length (key pattern may vary by Celery version)

## 10. Unit Tests

```bash
# Run pytest inside the web container
docker compose exec web pytest tests/ -v
```

---

# Credit Model

```
available_credits = total_credits - reserved_credits - spent_credits
```

**Flow:**
1. **Task submitted** → Credits reserved (available → reserved)
2. **Task succeeds** → Credits committed (reserved → spent)
3. **Task fails** → Credits rolled back (reserved → available)

**Users are only charged for successfully completed work.**

---

# FAQ

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

## Queue Durability

**Q: The Celery task queue is in Redis. What if we lose it?**

The queue (`celery:task_queue`) is ephemeral. If Redis crashes and loses the queue:

1. **Tasks in Postgres are the source of truth** - they have status `pending` or `in_progress`
2. **Recovery process** reconstructs the queue:
   ```sql
   SELECT * FROM tasks
   WHERE status IN ('pending', 'in_progress')
   ORDER BY created_at ASC;  -- FIFO order preserved
   ```
3. Re-enqueue each task to Celery
4. Workers' **idempotency check** prevents duplicate processing (checks Postgres status before work)

The `created_at` timestamp ensures we maintain FIFO order when reconstructing the queue.

**Q: Why not use a durable queue like RabbitMQ?**

For AI inference workloads:
- Tasks are already durable in Postgres
- Redis queue loss is recoverable (see above)
- Redis is faster for the polling hot path
- Simpler architecture (one fewer service)

Trade-off: Brief recovery delay vs. always-on durability. Acceptable for async workloads.
