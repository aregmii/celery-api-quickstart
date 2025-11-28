"""
Business logic for task operations.

TODO: Metrics to implement for production:
- METRIC: task_submissions_total (Counter) - Total tasks submitted
  - Labels: user_name, status (success/insufficient_credits)
  - Alarm: If error rate > 5% over 5 minutes, alert on-call

- METRIC: task_processing_duration_seconds (Histogram) - Time from submit to complete
  - Alarm: If p99 > 10s, investigate worker performance

- METRIC: credits_remaining (Gauge) - User credit balance after deduction
  - Alarm: If any user drops below 10 credits, notify billing team

Integration options:
- Prometheus: Use prometheus_client library
- Datadog: Use ddtrace library
- CloudWatch: Use aws-embedded-metrics library
"""

import uuid
from models.domain import User, Task
from repositories.user_repository import UserRepository
from repositories.task_repository import TaskRepository
from services.auth_service import AuthService
from exceptions import InsufficientCreditsError, TaskNotFoundError
from logger import get_logger

logger = get_logger(__name__)


class TaskService:
    """Business logic for task operations."""
    
    def __init__(
        self,
        user_repo: UserRepository,
        task_repo: TaskRepository,
        auth_service: AuthService
    ):
        self.user_repo = user_repo
        self.task_repo = task_repo
        self.auth_service = auth_service
    
    async def submit_task(self, user: User, a: int, b: int) -> str:
        """
        Submit a new task for processing.
        
        1. Deduct credit from user
        2. Create task in database
        3. Enqueue to Celery
        
        Returns task_id.
        Raises InsufficientCreditsError if no credits.
        """
        # Import here to avoid circular dependency
        from worker.tasks import compute_task
        
        # Deduct credit
        remaining = await self.user_repo.deduct_credit_by_api_key(user.api_key)
        if remaining is None:
            raise InsufficientCreditsError()
        
        # Create task record
        task_id = str(uuid.uuid4())
        await self.task_repo.create_task(task_id, user.api_key, a, b)
        
        # Enqueue to Celery
        compute_task.delay(task_id, a, b)
        logger.info(f"Enqueued task={task_id[:8]}... for user={user.name}")
        
        return task_id
    
    async def get_task_by_id_for_user(self, task_id: str, user: User) -> Task:
        """
        Get task by ID with ownership check.
        
        Returns Task if found and owned by user.
        Raises TaskNotFoundError if not found or not owned.
        """
        task = await self.task_repo.get_task_by_id(task_id)
        
        if not task or task.owner_api_key != user.api_key:
            logger.warning(f"Task not found or not owned: task={task_id[:8]}... user={user.name}")
            raise TaskNotFoundError()
        
        return task