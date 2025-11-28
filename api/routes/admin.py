from fastapi import APIRouter, Depends, HTTPException
from models.domain import User
from models.requests import AdminCreditsRequest
from models.responses import MessageResponse
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from dependencies import get_current_user, get_user_repo, get_auth_service

router = APIRouter()


@router.post("/credits", response_model=MessageResponse)
async def update_user_credits(
    request: AdminCreditsRequest,
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    auth_service: AuthService = Depends(get_auth_service)
):
    """
    Admin endpoint to update a user's credits.
    
    Only the 'admin' user can call this endpoint.
    """
    # Authorization check
    if user.name != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Update credits
    success = await user_repo.update_credits_by_api_key(
        request.user_api_key,
        request.credits
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Invalidate cache
    auth_service.invalidate_cache_by_api_key(request.user_api_key)
    
    return MessageResponse(message="Credits updated")