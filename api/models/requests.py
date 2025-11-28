from pydantic import BaseModel


class TaskRequest(BaseModel):
    """Request body for POST /task"""
    a: int
    b: int


class AdminCreditsRequest(BaseModel):
    """Request body for POST /admin/credits"""
    user_api_key: str
    credits: int