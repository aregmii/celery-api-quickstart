from pydantic import BaseModel
from typing import Optional


class TaskResponse(BaseModel):
    """Response for POST /task"""
    task_id: str


class PollResponse(BaseModel):
    """Response for GET /poll/{task_id}"""
    status: str
    result: Optional[int] = None
    error_message: Optional[str] = None


class MessageResponse(BaseModel):
    """Generic message response"""
    message: str


class HealthResponse(BaseModel):
    """Response for GET /health"""
    status: str