# Sequence Diagrams

## 1. Task Submission Flow

`POST /task` - Includes authentication, credit validation, and task creation.

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant Redis
    participant Postgres
    participant Celery as Celery Queue

    Client->>FastAPI: POST /task {a: 5, b: 3}<br/>Authorization: Bearer <api_key>

    %% Authentication
    FastAPI->>Redis: GET user:<api_key>
    alt Cache HIT
        Redis-->>FastAPI: User JSON
    else Cache MISS
        Redis-->>FastAPI: null
        FastAPI->>Postgres: SELECT * FROM users WHERE api_key = $1
        Postgres-->>FastAPI: User row
        FastAPI->>Redis: SETEX user:<api_key> 15 <json>
    end

    alt Invalid API key
        FastAPI-->>Client: 401 Invalid API key
    end

    %% Credit Deduction (atomic)
    FastAPI->>Postgres: UPDATE users SET credits = credits - 1<br/>WHERE api_key = $1 AND credits >= 1<br/>RETURNING credits

    alt Insufficient credits
        Postgres-->>FastAPI: 0 rows
        FastAPI-->>Client: 402 Insufficient credits
    else Has credits
        Postgres-->>FastAPI: remaining credits
    end

    %% Task Creation
    FastAPI->>Postgres: INSERT INTO tasks (id, owner_api_key, a, b, status='pending')
    FastAPI->>Celery: compute_task.delay(task_id, a, b)
    Note over Celery: Message stored in Redis queue

    FastAPI-->>Client: 202 {task_id: "uuid"}
```

---

## 2. Worker Processing Flow

Celery worker picks up tasks from Redis queue and processes them.

```mermaid
sequenceDiagram
    participant Redis as Redis (Celery Broker)
    participant Worker as Celery Worker
    participant Postgres

    loop Continuous polling
        Worker->>Redis: BRPOP celery queue

        alt Task available
            Redis-->>Worker: {task_id, a, b}

            Worker->>Postgres: UPDATE tasks SET status = 'running'<br/>WHERE id = $1

            Worker->>Worker: Process task (a + b)

            alt Success
                Worker->>Postgres: UPDATE tasks SET status = 'complete',<br/>result = $1 WHERE id = $2
            else Failure (retries exhausted)
                Worker->>Postgres: UPDATE tasks SET status = 'failed',<br/>error_message = $1 WHERE id = $2
            else Failure (retries remaining)
                Worker->>Redis: Re-queue with exponential backoff
            end

        else Queue empty
            Note over Worker: Block waiting for next task
        end
    end
```

---

## 3. Poll for Result Flow

`GET /poll/{task_id}` - Check task status with ownership validation.

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant Redis
    participant Postgres

    Client->>FastAPI: GET /poll/{task_id}<br/>Authorization: Bearer <api_key>

    %% Authentication (same cache-aside pattern)
    FastAPI->>Redis: GET user:<api_key>
    alt Cache HIT
        Redis-->>FastAPI: User JSON
    else Cache MISS
        FastAPI->>Postgres: SELECT * FROM users WHERE api_key = $1
        Postgres-->>FastAPI: User row
        FastAPI->>Redis: SETEX user:<api_key> 15 <json>
    end

    alt Invalid API key
        FastAPI-->>Client: 401 Invalid API key
    end

    %% Fetch Task
    FastAPI->>Postgres: SELECT * FROM tasks WHERE id = $1
    Postgres-->>FastAPI: Task row (or null)

    alt Task not found OR task.owner_api_key != user.api_key
        FastAPI-->>Client: 404 Task not found
        Note over FastAPI: Returns 404 (not 403) to prevent<br/>leaking task existence
    else Task owned by user
        FastAPI-->>Client: 200 {status, result, error_message}
    end
```

---

## 4. Admin Update Credits Flow

`POST /admin/credits` - Admin-only endpoint to set user credits.

```mermaid
sequenceDiagram
    participant Admin
    participant FastAPI
    participant Redis
    participant Postgres

    Admin->>FastAPI: POST /admin/credits<br/>{user_api_key: "target", credits: 1000}<br/>Authorization: Bearer <admin_api_key>

    %% Authenticate the admin
    FastAPI->>Redis: GET user:<admin_api_key>
    alt Cache HIT
        Redis-->>FastAPI: User JSON
    else Cache MISS
        FastAPI->>Postgres: SELECT * FROM users WHERE api_key = $1
        Postgres-->>FastAPI: User row
        FastAPI->>Redis: SETEX user:<admin_api_key> 15 <json>
    end

    %% Authorization check
    alt user.name != "admin"
        FastAPI-->>Admin: 403 Admin access required
    end

    %% Update target user's credits
    FastAPI->>Postgres: UPDATE users SET credits = $1<br/>WHERE api_key = $2

    alt User not found
        Postgres-->>FastAPI: 0 rows affected
        FastAPI-->>Admin: 404 User not found
    else Success
        Postgres-->>FastAPI: 1 row affected
        FastAPI->>Redis: DEL user:<target_api_key>
        Note over Redis: Invalidate cache so next auth<br/>fetches fresh credits from DB
        FastAPI-->>Admin: 200 {message: "Credits updated"}
    end
```

---

## Storage Responsibilities

| Store | Data | Purpose |
|-------|------|---------|
| **PostgreSQL** | `users`, `tasks` | Persistent source of truth |
| **Redis** | `user:<api_key>` | Auth cache (15s TTL) |
| **Redis** | Celery queues | Task message broker |
