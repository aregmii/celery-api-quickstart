from typing import Optional
from asyncpg import Pool
from models.domain import User
from logger import get_logger

logger = get_logger(__name__)


class UserRepository:
    """Database operations for users table."""
    
    def __init__(self, db_pool: Pool):
        self.db_pool = db_pool
    
    async def get_user_by_api_key(self, api_key: str) -> Optional[User]:
        """Fetch user by API key. Returns None if not found."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, api_key, credits FROM users WHERE api_key = $1",
                api_key
            )
        
        if not row:
            return None
        
        return User(
            name=row["name"],
            api_key=row["api_key"],
            credits=row["credits"]
        )
    
    async def deduct_credit_by_api_key(self, api_key: str) -> Optional[int]:
        """
        Atomically deduct 1 credit from user.
        
        Returns remaining credits, or None if insufficient credits.
        """
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users SET credits = credits - 1
                WHERE api_key = $1 AND credits >= 1
                RETURNING credits
                """,
                api_key
            )
        
        if not row:
            logger.warning(f"Insufficient credits for api_key={api_key[:8]}...")
            return None
        
        logger.info(f"Deducted credit for api_key={api_key[:8]}..., remaining={row['credits']}")
        return row["credits"]
    
    async def update_credits_by_api_key(self, api_key: str, credits: int) -> bool:
        """
        Set user's credits to a specific value.
        
        Returns True if user was found and updated, False otherwise.
        """
        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET credits = $1 WHERE api_key = $2",
                credits, api_key
            )
        
        success = result != "UPDATE 0"
        if success:
            logger.info(f"Updated credits for api_key={api_key[:8]}... to {credits}")
        else:
            logger.warning(f"User not found: api_key={api_key[:8]}...")
        
        return success