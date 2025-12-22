# Demo Guide

## Before Your Demo (IMPORTANT!)

```bash
# Reset everything - clean slate
docker compose down -v

# Start fresh
docker compose up -d

# Wait 10 seconds, then verify all 5 services running
docker compose ps
```

---

## Test API Keys

| User | API Key | Credits |
|------|---------|---------|
| **admin** | `123e4567-e89b-12d3-a456-426614174000` | 1000 |
| **test_user1** | `550e8400-e29b-41d4-a716-446655440000` | 500 |
| **test_user2** | `c56a4180-65aa-42ec-a945-5fd21dec0538` | 250 |

---

## Open These UIs (Before Starting Demo)

| UI | URL | What to Show |
|----|-----|--------------|
| **Flower** | http://localhost:5555 | Worker activity, task queue |
| **Terminal** | `docker compose logs -f worker` | Real-time worker logs |

---

## Demo Flow (Follow This Order)

### Step 1: Health Check

```bash
curl http://localhost:8000/health
```

**Expected:** `{"status":"healthy"}`

**Say:** "Basic health check, used by load balancers."

---

### Step 2: Submit a Task

```bash
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"a": 5, "b": 3}'
```

**Expected:** `{"task_id":"<uuid>"}`

**Copy the task_id!**

**Say:** "Task submitted. Before queuing, we validated: auth token, available credits, input types. Credits are now reserved atomically via Lua script."

**Show in Flower:** Click "Tasks" tab - you'll see the task appear.

**Show in Terminal:** Worker logs show task picked up.

---

### Step 3: Poll for Result (Immediately)

```bash
curl http://localhost:8000/poll/<TASK_ID> \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

**Expected (if fast):** `{"status":"pending","result":null,"error_message":null}`

**Say:** "Polling hits Redis first - single call returns task with ownership check. No separate auth lookup."

---

### Step 4: Poll Again (After ~2 seconds)

```bash
curl http://localhost:8000/poll/<TASK_ID> \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

**Expected:** `{"status":"complete","result":8,"error_message":null}`

**Say:** "Task complete. 5 + 3 = 8. Credits moved from reserved to spent. Worker wrote to Postgres first (durable), then Redis (fast reads)."

**Show in Flower:** Task shows SUCCESS status.

---

### Step 5: Check Database State

**Show Postgres (users):**
```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT name, total_credits, spent_credits FROM users;"
```

**Say:** "test_user1 now has spent_credits = 1. Postgres is source of truth."

**Show Postgres (tasks):**
```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT id, status, a, b, result FROM tasks ORDER BY created_at DESC LIMIT 3;"
```

**Say:** "Task persisted with complete status and result."

---

### Step 6: Admin Updates Credits

```bash
curl -X POST http://localhost:8000/admin/credits \
  -H "Authorization: Bearer 123e4567-e89b-12d3-a456-426614174000" \
  -H "Content-Type: application/json" \
  -d '{"user_api_key": "550e8400-e29b-41d4-a716-446655440000", "credits": 2000}'
```

**Expected:** `{"message":"Credits updated"}`

**Say:** "Admin endpoint. Writes to Postgres first, then Redis. Both must succeed or request fails."

**Verify:**
```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT name, total_credits FROM users WHERE name = 'test_user1';"
```

---

### Step 7: Error Cases

**Invalid API Key (401):**
```bash
curl -X POST http://localhost:8000/task \
  -H "Authorization: Bearer invalid-key" \
  -H "Content-Type: application/json" \
  -d '{"a": 1, "b": 2}'
```

**Expected:** `{"detail":"Invalid API key"}`

**Say:** "Auth fails fast - checked in Redis first, then Postgres fallback. Invalid key never reaches the queue."

---

**Non-Admin Access (403):**
```bash
curl -X POST http://localhost:8000/admin/credits \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"user_api_key": "test", "credits": 100}'
```

**Expected:** `{"detail":"Admin access required"}`

**Say:** "Authorization check - only admin user can update credits."

---

**Missing Auth (403):**
```bash
curl -X POST http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{"a": 1, "b": 2}'
```

**Expected:** `{"detail":"Not authenticated"}`

---

### Step 8: Load Test (Show Queue Building)

```bash
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/task \
    -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
    -H "Content-Type: application/json" \
    -d "{\"a\": $i, \"b\": $i}" &
done
wait
```

**Show in Flower:**
- Click "Tasks" - see multiple tasks queued
- Watch them process one at a time (worker_prefetch_multiplier=1)

**Show in Terminal (worker logs):**
- Tasks processing sequentially
- Each takes ~2 seconds

**Say:** "Worker processes one task at a time - critical for GPU workloads. Queue builds up, workers drain it. This is how we'd scale horizontally."

---

### Step 9: Show Structured Logs

```bash
docker compose logs web --tail=20
```

**Say:** "All logs are structured JSON with request_id for distributed tracing. Ready for Datadog/Splunk/ELK."

---

## Quick Reference Commands

### Reset Everything
```bash
docker compose down -v && docker compose up -d
```

### Watch Worker Logs
```bash
docker compose logs -f worker
```

### Watch API Logs
```bash
docker compose logs -f web
```

### Check All Services
```bash
docker compose ps
```

### View Users in Postgres
```bash
docker compose exec postgres psql -U postgres -c "SELECT * FROM users;"
```

### View Tasks in Postgres
```bash
docker compose exec postgres psql -U postgres -c "SELECT id, status, result FROM tasks ORDER BY created_at DESC LIMIT 5;"
```

---

## If Something Goes Wrong

**Services not starting?**
```bash
docker compose down -v
docker compose up -d
```

**Task stuck in pending?**
```bash
docker compose logs worker
# Check if worker is connected
```

**Port already in use?**
```bash
lsof -i :8000
# Kill the process or restart Docker
```

---

## Key Points to Mention During Demo

1. **Speed first:** Redis handles all hot-path operations
2. **Atomic credits:** Lua scripts prevent race conditions
3. **Input validation:** All checks happen BEFORE queuing
4. **One task at a time:** `worker_prefetch_multiplier=1` for GPU workloads
5. **Late ack:** If worker crashes, task retries automatically
6. **Postgres = truth:** Redis is just a fast cache
7. **Structured logs:** Ready for production observability
