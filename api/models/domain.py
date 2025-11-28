from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class User(BaseModel):
    """Domain model for a user."""
    name: str
    api_key: str
    credits: int


class Task(BaseModel):
    """Domain model for a task."""
    id: str
    owner_api_key: str
    a: int
    b: int
    status: str
    result: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None