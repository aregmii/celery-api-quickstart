import json
from typing import Optional
import redis
from repositories.user_repository import UserRepository
from models.domain import User
from config import settings
from logger import get_logger
from exceptions import InvalidApiKeyError

logger = get_logger(__name__)


class AuthService:
    """Authentication service with Redis caching."""
    
    def __init__(self, cache: redis.Redis, user_repo: UserRepository):
        self.cache = cache
        self.user_repo = user_repo
        self.cache_ttl = settings.cache_ttl_seconds
    
    async def get_user_by_api_key(self, api_key: str) -> User:
        """
        Get user by API key using cache-aside pattern.
        
        1. Check Redis cache
        2. If miss, query database
        3. Cache the result
        
        Raises InvalidApiKeyError if user not found.
        """
        cache_key = f"user:{api_key}"
        
        # Check cache
        try:
            cached = self.cache.get(cache_key)
            if cached:
                logger.info(f"Cache HIT for api_key={api_key[:8]}...")
                return User.model_validate_json(cached)
            logger.info(f"Cache MISS for api_key={api_key[:8]}...")
        except redis.RedisError as e:
            logger.warning(f"Cache read error: {e}")
        
        # Cache miss - query database
        user = await self.user_repo.get_user_by_api_key(api_key)
        if not user:
            logger.warning(f"Invalid api_key={api_key[:8]}...")
            raise InvalidApiKeyError()
        
        # Populate cache
        try:
            self.cache.setex(cache_key, self.cache_ttl, user.model_dump_json())
            logger.info(f"Cached user={user.name} for {self.cache_ttl}s")
        except redis.RedisError as e:
            logger.warning(f"Cache write error: {e}")
        
        return user
    
    def invalidate_cache_by_api_key(self, api_key: str) -> None:
        """Delete cached user data."""
        cache_key = f"user:{api_key}"
        try:
            self.cache.delete(cache_key)
            logger.info(f"Invalidated cache for api_key={api_key[:8]}...")
        except redis.RedisError as e:
            logger.warning(f"Cache invalidation error: {e}")