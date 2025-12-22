# Sequence Diagrams

## Architecture Overview

### Storage Responsibilities

| Store | Data | Purpose |
|-------|------|---------|
| **Redis** | `user:<api_key>` hash | Real-time credit tracking (available, reserved) |
| **Redis** | `task:<task_id>` hash | Task state and metadata |
| **Redis** | Celery task queue | Task message broker |
| **PostgreSQL** | `users`, `tasks` | Persistent storage, audit log, recovery source |

### Credit Model

```
available_credits = total_credits - reserved_credits

Example: User has 3 total credits
- Job A (2 credits) arrives  → reserve 2  → available: 1, reserved: 2
- Job B (2 credits) arrives  → reject     → available: 1 < required: 2
- Job A completes            → commit 2   → total: 1, reserved: 0
```

---

## 1. Task Submission Flow

`POST /task` - Single Redis call for auth + credit reservation.

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant Redis
    participant Postgres
    participant Celery as Celery Queue

    Client->>FastAPI: POST /task {a: 5, b: 3, credits_required: 2}<br/>Authorization: Bearer <api_key>

    %% Single atomic operation: validate user + reserve credits
    FastAPI->>Redis: EVALSHA reserve_credits_script<br/>args: api_key, credits_required

    Note over Redis: Lua script (atomic):<br/>1. Check user exists<br/>2. Check available >= required<br/>3. Increment reserved_credits<br/>4. Return success/failure

    alt User not found
        Redis-->>FastAPI: {error: "invalid_key"}
        FastAPI-->>Client: 401 Invalid API key
    else Insufficient credits
        Redis-->>FastAPI: {error: "insufficient", available: 1, required: 2}
        FastAPI-->>Client: 402 Insufficient credits
    else Success
        Redis-->>FastAPI: {ok: true, reservation_id: "uuid"}
    end

    %% Store task in Redis
    FastAPI->>Redis: HSET task:<task_id><br/>owner, a, b, status=pending, reservation_id

    %% Enqueue to Celery
    FastAPI->>Redis: Push to Celery queue:<br/>{task_id, a, b, reservation_id}

    %% Async: persist to Postgres (non-blocking)
    FastAPI-->>Postgres: INSERT task (async, fire-and-forget)

    FastAPI-->>Client: 202 {task_id: "uuid"}
```

---

## 2. Worker Processing Flow

Worker processes task, then commits or releases reserved credits.

```mermaid
sequenceDiagram
    participant Celery as Celery Queue
    participant Worker
    participant Redis
    participant Postgres

    Worker->>Celery: Poll for next task

    alt Task available
        Celery-->>Worker: {task_id, a, b, reservation_id}

        Worker->>Redis: HSET task:<task_id> status=running

        Worker->>Worker: Process task (a + b)

        alt Success
            %% Commit: deduct from total, clear reservation
            Worker->>Redis: EVALSHA commit_credits_script<br/>args: api_key, reservation_id, credits

            Note over Redis: Lua script (atomic):<br/>1. Subtract credits from total<br/>2. Subtract from reserved<br/>3. Delete reservation

            Worker->>Redis: HSET task:<task_id><br/>status=complete, result=8

            %% Persist to Postgres
            Worker->>Postgres: UPDATE tasks SET status=complete, result=8

        else Failure (retries exhausted)
            %% Release: clear reservation, no deduction
            Worker->>Redis: EVALSHA release_credits_script<br/>args: api_key, reservation_id, credits

            Note over Redis: Lua script (atomic):<br/>1. Subtract from reserved only<br/>2. Delete reservation<br/>3. Credits restored to available

            Worker->>Redis: HSET task:<task_id><br/>status=failed, error=<message>

            Worker->>Postgres: UPDATE tasks SET status=failed

        else Failure (retries remaining)
            Note over Worker: Keep reservation active
            Worker->>Celery: Re-queue with backoff
        end

    else Queue empty
        Note over Worker: Wait for next task
    end
```

---

## 3. Poll for Result Flow

`GET /poll/{task_id}` - Fast Redis lookup, no Postgres needed.

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant Redis

    Client->>FastAPI: GET /poll/{task_id}<br/>Authorization: Bearer <api_key>

    %% Single Redis call: get user + task
    FastAPI->>Redis: HGET user:<api_key> (exists check)

    alt User not found
        Redis-->>FastAPI: null
        FastAPI-->>Client: 401 Invalid API key
    end

    FastAPI->>Redis: HGETALL task:<task_id>
    Redis-->>FastAPI: {owner, status, result, error}

    alt Task not found
        FastAPI-->>Client: 404 Task not found
    else Task owner != api_key
        FastAPI-->>Client: 404 Task not found
        Note over FastAPI: Returns 404 (not 403) to hide task existence
    else Owned by user
        FastAPI-->>Client: 200 {status, result, error_message}
    end
```

---

## 4. Admin Update Credits Flow

`POST /admin/credits` - Update Redis (real-time) and Postgres (persistent).

```mermaid
sequenceDiagram
    participant Admin
    participant FastAPI
    participant Redis
    participant Postgres

    Admin->>FastAPI: POST /admin/credits<br/>{user_api_key: "target", credits: 1000}<br/>Authorization: Bearer <admin_api_key>

    %% Verify admin
    FastAPI->>Redis: HGET user:<admin_api_key> name
    Redis-->>FastAPI: "admin" or other

    alt Not admin user
        FastAPI-->>Admin: 403 Admin access required
    end

    %% Check target user exists
    FastAPI->>Redis: EXISTS user:<target_api_key>

    alt User not found
        FastAPI-->>Admin: 404 User not found
    end

    %% Update Redis (real-time)
    FastAPI->>Redis: HSET user:<target_api_key> total_credits=1000

    %% Update Postgres (persistent)
    FastAPI->>Postgres: UPDATE users SET credits=1000 WHERE api_key=$1

    FastAPI-->>Admin: 200 {message: "Credits updated"}
```

---

## 5. Startup Sync Flow

On API startup, sync Redis from Postgres to ensure consistency.

```mermaid
sequenceDiagram
    participant FastAPI
    participant Postgres
    participant Redis

    Note over FastAPI: Application startup

    FastAPI->>Postgres: SELECT * FROM users

    loop For each user
        Postgres-->>FastAPI: {api_key, name, credits}
        FastAPI->>Redis: HSET user:<api_key><br/>name, total_credits, reserved_credits=0
    end

    FastAPI->>Postgres: SELECT * FROM tasks WHERE status IN ('pending', 'running')

    loop For each in-flight task
        Postgres-->>FastAPI: {task_id, owner, status, ...}
        FastAPI->>Redis: HSET task:<task_id> ...
        Note over FastAPI: Restore credit reservations for in-flight tasks
    end

    Note over FastAPI: Redis now mirrors Postgres
```

---

## Race Condition Example

**Scenario:** User has 3 credits. Two requests arrive simultaneously, each needing 2 credits.

```mermaid
sequenceDiagram
    participant Req1 as Request 1 (2 credits)
    participant Req2 as Request 2 (2 credits)
    participant Redis

    Note over Redis: State: total=3, reserved=0, available=3

    Req1->>Redis: EVALSHA reserve_credits(2)
    Note over Redis: Lua script executes atomically

    alt Req1 wins the race
        Note over Redis: reserved += 2<br/>State: total=3, reserved=2, available=1
        Redis-->>Req1: {ok: true}

        Req2->>Redis: EVALSHA reserve_credits(2)
        Note over Redis: available(1) < required(2)
        Redis-->>Req2: {error: "insufficient", available: 1}

    else Req2 wins the race
        Note over Redis: reserved += 2<br/>State: total=3, reserved=2, available=1
        Redis-->>Req2: {ok: true}

        Req1->>Redis: EVALSHA reserve_credits(2)
        Note over Redis: available(1) < required(2)
        Redis-->>Req1: {error: "insufficient", available: 1}
    end

    Note over Redis: Only one request succeeds.<br/>No overselling.
```

---

## Lua Scripts (Redis Atomic Operations)

### reserve_credits

```lua
-- KEYS[1] = user:<api_key>
-- ARGV[1] = credits_required
-- ARGV[2] = reservation_id

local total = tonumber(redis.call('HGET', KEYS[1], 'total_credits'))
if not total then
    return {err = 'invalid_key'}
end

local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved_credits') or 0)
local available = total - reserved
local required = tonumber(ARGV[1])

if available < required then
    return {err = 'insufficient', available = available, required = required}
end

redis.call('HINCRBY', KEYS[1], 'reserved_credits', required)
redis.call('HSET', 'reservation:' .. ARGV[2], 'api_key', KEYS[1], 'credits', required)

return {ok = true, reservation_id = ARGV[2]}
```

### commit_credits

```lua
-- Called on task SUCCESS: deduct from total, clear reservation
local api_key = redis.call('HGET', 'reservation:' .. ARGV[1], 'api_key')
local credits = tonumber(redis.call('HGET', 'reservation:' .. ARGV[1], 'credits'))

redis.call('HINCRBY', api_key, 'total_credits', -credits)
redis.call('HINCRBY', api_key, 'reserved_credits', -credits)
redis.call('DEL', 'reservation:' .. ARGV[1])

return {ok = true}
```

### release_credits

```lua
-- Called on task FAILURE: just clear reservation, credits restored
local api_key = redis.call('HGET', 'reservation:' .. ARGV[1], 'api_key')
local credits = tonumber(redis.call('HGET', 'reservation:' .. ARGV[1], 'credits'))

redis.call('HINCRBY', api_key, 'reserved_credits', -credits)
redis.call('DEL', 'reservation:' .. ARGV[1])

return {ok = true}
```
