"""
Business logic for task operations.

Implements the credit reservation flow:
1. Reserve credits in Redis (atomic)
2. Create task in Redis
3. Persist to Postgres (synchronous)
4. Queue to Celery
5. Return task_id

Rollback on any failure.
"""
import uuid
from models.schemas import User, Task
from repositories.task_repository import TaskRepository
from services.redis_service import RedisService
from exceptions import InsufficientCreditsError, TaskNotFoundError, InvalidApiKeyError
from logger import get_logger

logger = get_logger(__name__)


class TaskService:
    """Business logic for task operations."""

    def __init__(
        self,
        task_repo: TaskRepository,
        redis_service: RedisService
    ):
        self.task_repo = task_repo
        self.redis_service = redis_service

    async def submit_task(self, user: User, a: int, b: int, credits_required: int = 1) -> str:
        """
        Submit a new task for processing with proper reservation and rollback.

        Optimized Flow (3 network calls instead of 5):
        1. Reserve credits + create task cache in Redis (single atomic Lua script)
        2. Persist to Postgres (synchronous for durability)
        3. Push to Celery queue

        Trade-offs considered:
        - Combined Lua script saves 1 Redis round-trip
        - Postgres write is synchronous (not async) to ensure durability
        - Could batch multiple task submissions for further optimization

        Raises:
            InsufficientCreditsError: If user doesn't have enough credits
            InvalidApiKeyError: If user not found in Redis
        """
        from worker.tasks import compute_task

        task_id = str(uuid.uuid4())
        reservation_id = None

        try:
            # STEP 1: Reserve credits + create task cache (single atomic Lua script)
            # This validates user exists, has sufficient credits, reserves them,
            # creates reservation record, AND creates task cache - all in ONE call
            reservation_id = self.redis_service.reserve_credits_and_create_task(
                api_key=user.api_key,
                credits=credits_required,
                task_id=task_id,
                a=a,
                b=b
            )
            if reservation_id is None:
                raise Exception("Credit reservation failed unexpectedly")

            logger.info(f"Reserved {credits_required} credits + created task={task_id[:8]}...")

            # STEP 2: Persist to Postgres (SYNCHRONOUS - must succeed for durability)
            # Trade-off: Could be async for speed, but risks data loss if Redis crashes
            await self.task_repo.create_task(
                task_id=task_id,
                owner_api_key=user.api_key,
                a=a,
                b=b,
                credits_required=credits_required
            )

            # STEP 3: Enqueue to Celery
            compute_task.delay(task_id, a, b, reservation_id)

            logger.info(f"Submitted task={task_id[:8]}... for user={user.name}")
            return task_id

        except (InsufficientCreditsError, InvalidApiKeyError):
            # These are expected errors - no rollback needed
            # (Lua script is atomic, nothing was committed)
            raise

        except Exception as e:
            # Rollback on any unexpected failure
            logger.error(f"Task submission failed, rolling back: {e}")

            if reservation_id:
                # This also deletes the task cache created by the Lua script
                self.redis_service.delete_task(task_id)
                self.redis_service.rollback_credits(reservation_id)

            raise

    async def get_task_by_id_for_user(self, task_id: str, user: User) -> Task:
        """
        Get task with Redis-first, Postgres-fallback pattern.

        Optimized: Single Redis call returns task with owner_api_key for ownership check.

        Returns Task if found and owned by user.
        Raises TaskNotFoundError if not found or not owned.
        """
        # Try Redis first (fast path)
        task_data = self.redis_service.get_task(task_id)

        if task_data:
            # Check ownership
            if task_data['owner_api_key'] != user.api_key:
                logger.warning(f"Task ownership mismatch: task={task_id[:8]}... user={user.name}")
                raise TaskNotFoundError()

            return Task(
                id=task_id,
                owner_api_key=task_data['owner_api_key'],
                a=task_data['a'],
                b=task_data['b'],
                credits_required=task_data['credits_required'],
                status=task_data['status'],
                result=task_data['result'],
                error_message=task_data['error_message'],
                reservation_id=task_data.get('reservation_id')
            )

        # Fallback to Postgres (cache miss or old task)
        task = await self.task_repo.get_task_by_id(task_id)

        if not task or task.owner_api_key != user.api_key:
            logger.warning(f"Task not found or not owned: task={task_id[:8]}... user={user.name}")
            raise TaskNotFoundError()

        return task
