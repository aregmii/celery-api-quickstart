from typing import Optional, List
from asyncpg import Pool
from models.schemas import Task
from logger import get_logger

logger = get_logger(__name__)


class TaskRepository:
    """Database operations for tasks table."""

    def __init__(self, db_pool: Pool):
        self.db_pool = db_pool

    async def create_task(
        self,
        task_id: str,
        owner_api_key: str,
        a: int,
        b: int,
        credits_required: int = 1
    ) -> None:
        """Insert a new task with status='pending'."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, owner_api_key, a, b, credits_required, status)
                VALUES ($1, $2, $3, $4, $5, 'pending')
                """,
                task_id, owner_api_key, a, b, credits_required
            )

        logger.info(f"Created task={task_id[:8]}... for api_key={owner_api_key[:8]}...")

    async def get_task_by_id(self, task_id: str) -> Optional[Task]:
        """Fetch task by ID. Returns None if not found."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, owner_api_key, a, b, credits_required, status, result, error_message,
                       created_at, updated_at
                FROM tasks WHERE id = $1
                """,
                task_id
            )

        if not row:
            return None

        return Task(
            id=str(row["id"]),
            owner_api_key=row["owner_api_key"],
            a=row["a"],
            b=row["b"],
            credits_required=row["credits_required"],
            status=row["status"],
            result=row["result"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    async def get_in_flight_tasks(self) -> List[Task]:
        """Get all pending/in_progress tasks for startup sync."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, owner_api_key, a, b, credits_required, status, result, error_message,
                       created_at, updated_at
                FROM tasks
                WHERE status IN ('pending', 'in_progress')
                """
            )

        return [
            Task(
                id=str(row["id"]),
                owner_api_key=row["owner_api_key"],
                a=row["a"],
                b=row["b"],
                credits_required=row["credits_required"],
                status=row["status"],
                result=row["result"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )
            for row in rows
        ]

    async def is_task_complete(self, task_id: str) -> Optional[str]:
        """
        Check if task is already complete or failed (for idempotency).

        Returns status if complete/failed, None otherwise.
        """
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM tasks WHERE id = $1",
                task_id
            )

        if row and row["status"] in ("complete", "failed"):
            return row["status"]
        return None

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update task status and optionally result/error."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tasks
                SET status = $1, result = $2, error_message = $3, updated_at = NOW()
                WHERE id = $4
                """,
                status, result, error_message, task_id
            )

        logger.info(f"Updated task={task_id[:8]}... status={status}")
