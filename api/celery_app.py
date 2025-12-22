from celery import Celery
from config import settings

celery_app = Celery(
    "tasks",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    
    # ML/Production Safeguards
    worker_max_tasks_per_child=50,  # Restart worker often to clear GPU/RAM leaks
    task_soft_time_limit=300,       # 5 min soft timeout
    task_time_limit=330,            # 5.5 min hard kill
    result_expires=86400,
)