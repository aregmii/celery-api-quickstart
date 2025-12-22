"""
FastAPI application entry point.

Startup sync: Only for RECOVERY when Redis is empty/crashed.
- Redis is shared/external - all pods connect to same instance
- If Redis has data → skip (normal operation)
- If Redis empty → populate from Postgres (recovery)
"""
import uuid
import asyncpg
import redis
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings
from logger import configure_logging, get_logger, request_id_ctx
from routes import router
from services.redis_service import RedisService
from repositories.user_repository import UserRepository
from repositories.task_repository import TaskRepository

configure_logging()
logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id_ctx.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


async def startup_sync(
    db_pool: asyncpg.Pool,
    redis_service: RedisService
) -> None:
    """
    Recovery sync: Populate Redis from Postgres if Redis is empty.

    When does this run?
    - Redis crashed/restarted → data lost → repopulate
    - Fresh deployment → Redis empty → seed data

    When does this NOT run?
    - Normal pod scaling → Redis already has data → skip
    """
    logger.info("sync: checking Redis state")

    user_repo = UserRepository(db_pool)
    task_repo = TaskRepository(db_pool)

    # Sync users: if not in Redis → add from Postgres
    users = await user_repo.get_all_users()
    users_added = 0
    for user in users:
        if not redis_service.user_exists(user.api_key):
            redis_service.set_user(
                api_key=user.api_key,
                name=user.name,
                total_credits=user.total_credits,
                spent_credits=user.spent_credits,
                reserved_credits=0
            )
            users_added += 1

    logger.info(f"sync: users {users_added} added, {len(users) - users_added} skipped")

    # Sync in-flight tasks: if not in Redis → add + create reservation
    in_flight_tasks = await task_repo.get_in_flight_tasks()
    tasks_added = 0

    for task in in_flight_tasks:
        if not redis_service.task_exists(task.id):
            reservation_id = str(uuid.uuid4())

            redis_service.create_task(
                task_id=task.id,
                owner_api_key=task.owner_api_key,
                a=task.a,
                b=task.b,
                credits_required=task.credits_required,
                reservation_id=reservation_id
            )

            redis_service.create_reservation(
                reservation_id=reservation_id,
                api_key=task.owner_api_key,
                credits=task.credits_required,
                task_id=task.id
            )
            redis_service.increment_reserved_credits(
                task.owner_api_key,
                task.credits_required
            )
            tasks_added += 1

    logger.info(f"sync: tasks {tasks_added} added, {len(in_flight_tasks) - tasks_added} skipped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting")

    # Postgres pool
    app.state.db_pool = await asyncpg.create_pool(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password,
        database=settings.database_name,
        min_size=2,
        max_size=10,
    )

    # Redis client (shared external service)
    app.state.redis_client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )
    app.state.redis_client.ping()

    app.state.redis_service = RedisService(app.state.redis_client)

    # Recovery sync (only if Redis missing data)
    try:
        await startup_sync(app.state.db_pool, app.state.redis_service)
    except Exception as e:
        logger.warning(f"sync failed: {e}, will populate on-demand")

    yield

    await app.state.db_pool.close()
    app.state.redis_client.close()
    logger.info("shutdown complete")


app = FastAPI(title="Task API", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.include_router(router)
