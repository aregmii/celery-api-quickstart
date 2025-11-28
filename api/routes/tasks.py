from fastapi import APIRouter, Depends, HTTPException
from models.domain import User
from models.requests import TaskRequest
from models.responses import TaskResponse, PollResponse
from services.task_service import TaskService
from dependencies import get_current_user, get_task_service
from exceptions import InsufficientCreditsError, TaskNotFoundError

router = APIRouter()


@router.post("/task", response_model=TaskResponse, status_code=202)
async def create_task(
    request: TaskRequest,
    user: User = Depends(get_current_user),
    task_service: TaskService = Depends(get_task_service)
):
    """
    Submit a task for async processing.
    
    Requires valid Bearer token and at least 1 credit.
    """
    try:
        task_id = await task_service.submit_task(user, request.a, request.b)
        return TaskResponse(task_id=task_id)
    except InsufficientCreditsError:
        raise HTTPException(status_code=402, detail="Insufficient credits")


@router.get("/poll/{task_id}", response_model=PollResponse)
async def poll_task(
    task_id: str,
    user: User = Depends(get_current_user),
    task_service: TaskService = Depends(get_task_service)
):
    """
    Poll for task status and result.
    
    Returns 404 if task not found or not owned by user.
    """
    try:
        task = await task_service.get_task_by_id_for_user(task_id, user)
        return PollResponse(
            status=task.status,
            result=task.result,
            error_message=task.error_message
        )
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail="Task not found")