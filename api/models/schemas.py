from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class User(BaseModel):
    """Domain model for a user."""
    name: str
    api_key: str
    total_credits: int
    spent_credits: int = 0
    reserved_credits: int = 0  # Only populated from Redis, not stored in Postgres


class Task(BaseModel):
    """Domain model for a task."""
    id: str
    owner_api_key: str
    a: int
    b: int
    credits_required: int = 1
    status: str  # pending, in_progress, complete, failed
    result: Optional[int] = None
    error_message: Optional[str] = None
    reservation_id: Optional[str] = None  # Only in Redis
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
