"""
Authentication service with Redis-first pattern.

Uses Redis hash for user data (not string cache).
Falls back to Postgres if Redis miss.
"""
from repositories.user_repository import UserRepository
from services.redis_service import RedisService
from models.schemas import User
from logger import get_logger
from exceptions import InvalidApiKeyError

logger = get_logger(__name__)


class AuthService:
    """Authentication service with Redis-first pattern."""

    def __init__(self, redis_service: RedisService, user_repo: UserRepository):
        self.redis_service = redis_service
        self.user_repo = user_repo

    async def get_user_by_api_key(self, api_key: str) -> User:
        """
        Get user by API key using Redis-first pattern.

        1. Check Redis hash (user:{api_key})
        2. If miss, query Postgres
        3. Populate Redis from Postgres (if found)

        Raises InvalidApiKeyError if user not found.
        """
        # Try Redis first (fast path)
        user_data = self.redis_service.get_user(api_key)
        if user_data:
            logger.info(f"Redis HIT for api_key={api_key[:8]}...")
            return User(
                name=user_data['name'],
                api_key=user_data['api_key'],
                total_credits=user_data['total_credits'],
                spent_credits=user_data['spent_credits'],
                reserved_credits=user_data['reserved_credits'],
            )

        logger.info(f"Redis MISS for api_key={api_key[:8]}...")

        # Fallback to Postgres
        user = await self.user_repo.get_user_by_api_key(api_key)
        if not user:
            logger.warning(f"Invalid api_key={api_key[:8]}...")
            raise InvalidApiKeyError()

        # Populate Redis from Postgres
        self.redis_service.set_user(
            api_key=user.api_key,
            name=user.name,
            total_credits=user.total_credits,
            spent_credits=user.spent_credits,
            reserved_credits=0  # No reservations tracked in Postgres
        )
        logger.info(f"Populated Redis for user={user.name}")

        # Return with reserved_credits from Redis (0 if just created)
        user.reserved_credits = 0
        return user

    def get_user_sync(self, api_key: str) -> User | None:
        """
        Synchronous user lookup from Redis only.

        Used when async not available (e.g., validation).
        Returns None if not found in Redis.
        """
        user_data = self.redis_service.get_user(api_key)
        if not user_data:
            return None

        return User(
            name=user_data['name'],
            api_key=user_data['api_key'],
            total_credits=user_data['total_credits'],
            spent_credits=user_data['spent_credits'],
            reserved_credits=user_data['reserved_credits'],
        )
