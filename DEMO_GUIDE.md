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

## Demo Flow

### Step 1: Health Check

```bash
curl http://localhost:8000/health
```

**Expected:** `{"status":"healthy"}`

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

**Verify:**
- Flower UI → Tasks tab → task appears
- Worker logs → task picked up

---

### Step 3: Poll for Result (Immediately)

```bash
curl http://localhost:8000/poll/<TASK_ID> \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

**Expected (if fast):** `{"status":"pending","result":null,"error_message":null}`

---

### Step 4: Poll Again (After ~2 seconds)

```bash
curl http://localhost:8000/poll/<TASK_ID> \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

**Expected:** `{"status":"complete","result":8,"error_message":null}`

**Verify:** Flower UI → task shows SUCCESS

---

### Step 5: Check Database State

**Users:**
```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT name, total_credits, spent_credits FROM users;"
```

**Verify:** test_user1 has spent_credits = 1

**Tasks:**
```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT id, status, a, b, result FROM tasks ORDER BY created_at DESC LIMIT 3;"
```

**Verify:** Task persisted with complete status and result

---

### Step 6: Admin Updates Credits

```bash
curl -X POST http://localhost:8000/admin/credits \
  -H "Authorization: Bearer 123e4567-e89b-12d3-a456-426614174000" \
  -H "Content-Type: application/json" \
  -d '{"user_api_key": "550e8400-e29b-41d4-a716-446655440000", "credits": 2000}'
```

**Expected:** `{"message":"Credits updated"}`

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

---

**Non-Admin Access (403):**
```bash
curl -X POST http://localhost:8000/admin/credits \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"user_api_key": "test", "credits": 100}'
```

**Expected:** `{"detail":"Admin access required"}`

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

**Verify:**
- Flower UI → Tasks tab → multiple tasks queued, consumed one at a time
- Worker logs → tasks processing sequentially (~2 seconds each)

---

### Step 9: Show Structured Logs

```bash
docker compose logs web --tail=20
```

**Verify:** Logs are structured JSON with request_id

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
```

**Port already in use?**
```bash
lsof -i :8000
```
