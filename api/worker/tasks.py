"""
Celery worker tasks.

Flow:
1. Idempotency check (if complete/failed → skip)
2. Mark in_progress (Postgres first, then Redis)
3. Compute result
4. On success: Postgres first → commit credits in Redis
5. On failure: Postgres first → rollback credits in Redis
"""
import time
import asyncio
import asyncpg
import redis
from celery_app import celery_app
from config import settings
from logger import get_logger

logger = get_logger(__name__)


# Redis client for worker
_redis_client = None


def get_redis_client() -> redis.Redis:
    """Get or create Redis client for worker."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
        )
    return _redis_client


async def get_db_connection():
    """Create a database connection for the worker."""
    return await asyncpg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password,
        database=settings.database_name,
    )


async def check_task_complete(task_id: str) -> str | None:
    """
    Idempotency check: Check if task is already complete or failed.

    Returns status if complete/failed, None otherwise.
    """
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT status FROM tasks WHERE id = $1",
            task_id
        )
        if row and row["status"] in ("complete", "failed"):
            return row["status"]
        return None
    finally:
        await conn.close()


async def update_task_in_postgres(
    task_id: str,
    status: str,
    result: int = None,
    error_message: str = None
):
    """Update task status in Postgres (source of truth)."""
    conn = await get_db_connection()
    try:
        await conn.execute(
            """
            UPDATE tasks
            SET status = $1, result = $2, error_message = $3, updated_at = NOW()
            WHERE id = $4
            """,
            status, result, error_message, task_id
        )
        logger.info(f"Postgres: task={task_id[:8]}... status={status}")
    finally:
        await conn.close()


async def commit_spent_credits_in_postgres(api_key: str, amount: int):
    """Commit spent_credits in Postgres (task succeeded)."""
    conn = await get_db_connection()
    try:
        await conn.execute(
            """
            UPDATE users SET spent_credits = spent_credits + $1
            WHERE api_key = $2
            """,
            amount, api_key
        )
        logger.info(f"Postgres: incremented spent_credits by {amount} for api_key={api_key[:8]}...")
    finally:
        await conn.close()


async def get_task_owner(task_id: str) -> str | None:
    """Get the owner api_key for a task from Postgres."""
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT owner_api_key FROM tasks WHERE id = $1",
            task_id
        )
        return row["owner_api_key"] if row else None
    finally:
        await conn.close()


def update_task_in_redis(
    task_id: str,
    status: str,
    result: int = None,
    error_message: str = None
):
    """Update task status in Redis (fast reads)."""
    try:
        r = get_redis_client()
        task_key = f"task:{task_id}"
        mapping = {'status': status}
        if result is not None:
            mapping['result'] = result
        if error_message is not None:
            mapping['error_message'] = error_message
        r.hset(task_key, mapping=mapping)
        logger.info(f"Redis: task={task_id[:8]}... status={status}")
    except redis.RedisError as e:
        # Redis failure is non-fatal - Postgres is source of truth
        logger.warning(f"Redis update failed (non-fatal): {e}")


def commit_credits_in_redis(reservation_id: str) -> bool:
    """
    Commit credits in Redis: move from reserved to spent.

    Returns True on success, False on failure.
    """
    if not reservation_id:
        return True  # No reservation to commit

    try:
        r = get_redis_client()
        reservation_key = f"reservation:{reservation_id}"

        # Get reservation details
        data = r.hgetall(reservation_key)
        if not data:
            logger.warning(f"Reservation not found: {reservation_id[:8]}...")
            return False

        user_key = data.get('user_key')
        credits = int(data.get('credits', 0))

        if not user_key or credits == 0:
            return False

        # Commit: move from reserved to spent
        pipe = r.pipeline()
        pipe.hincrby(user_key, 'reserved_credits', -credits)
        pipe.hincrby(user_key, 'spent_credits', credits)
        pipe.delete(reservation_key)
        pipe.execute()

        logger.info(f"Redis: committed {credits} credits, reservation={reservation_id[:8]}...")
        return True

    except redis.RedisError as e:
        logger.warning(f"Redis commit failed (non-fatal): {e}")
        return False


def rollback_credits_in_redis(reservation_id: str) -> bool:
    """
    Rollback credits in Redis: reserved → available (task failed).
    """
    if not reservation_id:
        return True  # No reservation to release

    try:
        r = get_redis_client()
        reservation_key = f"reservation:{reservation_id}"

        # Get reservation details
        data = r.hgetall(reservation_key)
        if not data:
            logger.warning(f"Reservation not found: {reservation_id[:8]}...")
            return False

        user_key = data.get('user_key')
        credits = int(data.get('credits', 0))

        if not user_key or credits == 0:
            return False

        # Release: decrease reserved credits
        pipe = r.pipeline()
        pipe.hincrby(user_key, 'reserved_credits', -credits)
        pipe.delete(reservation_key)
        pipe.execute()

        logger.info(f"Redis: released {credits} credits, reservation={reservation_id[:8]}...")
        return True

    except redis.RedisError as e:
        logger.warning(f"Redis release failed (non-fatal): {e}")
        return False


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=1,
    retry_backoff=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def compute_task(self, task_id: str, a: int, b: int, reservation_id: str = None):
    """
    Process a computation task.

    Flow:
    1. Idempotency check - skip if already complete/failed
    2. Mark as in_progress in both stores
    3. Compute result
    4. On success: Postgres first, then Redis (commit credits)
    5. On failure: Postgres first, then Redis (release credits)

    Runs in the worker container, not the API container.
    """
    logger.info(f"Processing task={task_id[:8]}...: {a} + {b}, reservation={reservation_id[:8] if reservation_id else 'none'}...")

    try:
        # STEP 1: Idempotency check
        existing_status = asyncio.run(check_task_complete(task_id))
        if existing_status:
            logger.info(f"Task={task_id[:8]}... already {existing_status}, skipping")
            # Sync Redis state if needed
            update_task_in_redis(task_id, existing_status)
            return None

        # STEP 2: Mark as in_progress (Postgres first)
        asyncio.run(update_task_in_postgres(task_id, "in_progress"))
        update_task_in_redis(task_id, "in_progress")

        # STEP 3: Simulate work (AI inference would happen here)
        time.sleep(2)

        # Compute result
        result = a + b

        # STEP 4: Success - update stores and commit credits
        # Get owner for spent_credits update
        owner_api_key = asyncio.run(get_task_owner(task_id))

        # Postgres FIRST (durable)
        asyncio.run(update_task_in_postgres(task_id, "complete", result=result))
        if owner_api_key:
            asyncio.run(commit_spent_credits_in_postgres(owner_api_key, 1))

        # Redis SECOND (fast reads)
        update_task_in_redis(task_id, "complete", result=result)
        commit_credits_in_redis(reservation_id)

        logger.info(f"Task={task_id[:8]}... complete: {result}")
        return result

    except Exception as exc:
        logger.error(f"Task={task_id[:8]}... failed: {exc}")

        # Check if we should retry
        if self.request.retries < self.max_retries:
            # Don't release credits yet - will retry
            raise self.retry(exc=exc)

        # Max retries exceeded - mark as failed and release credits
        logger.error(f"Task={task_id[:8]}... max retries exceeded, marking failed")

        # Postgres FIRST (durable)
        asyncio.run(update_task_in_postgres(task_id, "failed", error_message=str(exc)))

        # Redis SECOND (fast reads) - release credits
        update_task_in_redis(task_id, "failed", error_message=str(exc))
        rollback_credits_in_redis(reservation_id)

        raise
