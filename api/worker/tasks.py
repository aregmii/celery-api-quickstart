import time
import asyncio
import asyncpg
from celery_app import celery_app
from config import settings
from logger import get_logger

logger = get_logger(__name__)


async def get_db_connection():
    """Create a database connection for the worker."""
    return await asyncpg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password,
        database=settings.database_name,
    )


async def update_task_status_by_id(
    task_id: str,
    status: str,
    result: int = None,
    error_message: str = None
):
    """Update task status in database."""
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
    finally:
        await conn.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=1,
    retry_backoff=True,
    acks_late=True,
)
def compute_task(self, task_id: str, a: int, b: int):
    """
    Process a computation task.
    
    Runs in the worker container, not the API container.
    """
    logger.info(f"Processing task={task_id[:8]}...: {a} + {b}")
    
    try:
        # Update status to running
        asyncio.run(update_task_status_by_id(task_id, "running"))
        
        # Simulate work
        time.sleep(2)
        
        # Compute result
        result = a + b
        
        # Update database with result
        asyncio.run(update_task_status_by_id(task_id, "complete", result=result))
        
        logger.info(f"Task={task_id[:8]}... complete: {result}")
        return result
        
    except Exception as exc:
        logger.error(f"Task={task_id[:8]}... failed: {exc}")
        
        if self.request.retries >= self.max_retries:
            asyncio.run(update_task_status_by_id(task_id, "failed", error_message=str(exc)))
            raise
        
        raise self.retry(exc=exc)