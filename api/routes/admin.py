"""
Admin routes for user management.

Credit updates write to BOTH stores synchronously.
If Redis fails after Postgres succeeds, the request fails (prevents race condition).
"""
from fastapi import APIRouter, Depends, HTTPException
from models.schemas import User
from models.requests import AdminCreditsRequest
from models.responses import MessageResponse
from repositories.user_repository import UserRepository
from services.redis_service import RedisService
from dependencies import get_current_user, get_user_repo, get_redis_service
from logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/credits", response_model=MessageResponse)
async def update_user_credits(
    request: AdminCreditsRequest,
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    redis_service: RedisService = Depends(get_redis_service)
):
    """
    Admin endpoint to update a user's total_credits.

    Writes to BOTH stores synchronously:
    1. Update Postgres (source of truth)
    2. Update Redis (fast reads)

    If Redis fails after Postgres succeeds, request fails to prevent race condition.
    Only the 'admin' user can call this endpoint.
    """
    # Authorization check
    if user.name != "admin":
        logger.warning(f"Non-admin user={user.name} attempted admin operation")
        raise HTTPException(status_code=403, detail="Admin access required")

    target_api_key = request.user_api_key
    new_total_credits = request.credits

    logger.info(f"Admin updating credits for api_key={target_api_key[:8]}... to {new_total_credits}")

    # STEP 1: Update Postgres (source of truth)
    success = await user_repo.update_total_credits(target_api_key, new_total_credits)
    if not success:
        logger.warning(f"User not found in Postgres: api_key={target_api_key[:8]}...")
        raise HTTPException(status_code=404, detail="User not found")

    # STEP 2: Update Redis (must succeed to prevent race condition)
    redis_success = redis_service.update_user_total_credits(target_api_key, new_total_credits)
    if not redis_success:
        # Redis failed after Postgres succeeded
        # This is a critical inconsistency - fail the request
        logger.error(f"Redis update failed after Postgres success for api_key={target_api_key[:8]}...")
        raise HTTPException(
            status_code=500,
            detail="Credit update failed - Redis unavailable. Please retry."
        )

    logger.info(f"Credits updated for api_key={target_api_key[:8]}... to {new_total_credits}")
    return MessageResponse(message="Credits updated")
