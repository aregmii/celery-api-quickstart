import redis
from fastapi import Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from repositories.user_repository import UserRepository
from repositories.task_repository import TaskRepository
from services.auth_service import AuthService
from services.task_service import TaskService
from models.domain import User
from config import settings
from exceptions import InvalidApiKeyError

security = HTTPBearer()


def get_redis() -> redis.Redis:
    """Get Redis connection."""
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True
    )


def get_user_repo(request: Request) -> UserRepository:
    """Get UserRepository instance."""
    return UserRepository(request.app.state.db_pool)


def get_task_repo(request: Request) -> TaskRepository:
    """Get TaskRepository instance."""
    return TaskRepository(request.app.state.db_pool)


def get_auth_service(
    cache: redis.Redis = Depends(get_redis),
    user_repo: UserRepository = Depends(get_user_repo)
) -> AuthService:
    """Get AuthService instance."""
    return AuthService(cache, user_repo)


def get_task_service(
    user_repo: UserRepository = Depends(get_user_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    auth_service: AuthService = Depends(get_auth_service)
) -> TaskService:
    """Get TaskService instance."""
    return TaskService(user_repo, task_repo, auth_service)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    auth_service: AuthService = Depends(get_auth_service)
) -> User:
    """
    Dependency that validates bearer token and returns current user.
    
    Raises HTTPException 401 if invalid API key.
    """
    try:
        return await auth_service.get_user_by_api_key(credentials.credentials)
    except InvalidApiKeyError:
        raise HTTPException(status_code=401, detail="Invalid API key")