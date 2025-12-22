from typing import Optional, List
from asyncpg import Pool
from models.schemas import User
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
                "SELECT name, api_key, total_credits, spent_credits FROM users WHERE api_key = $1",
                api_key
            )

        if not row:
            return None

        return User(
            name=row["name"],
            api_key=row["api_key"],
            total_credits=row["total_credits"],
            spent_credits=row["spent_credits"],
            reserved_credits=0  # Will be populated from Redis if needed
        )

    async def get_all_users(self) -> List[User]:
        """Get all users for startup sync."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT name, api_key, total_credits, spent_credits FROM users"
            )

        return [
            User(
                name=row["name"],
                api_key=row["api_key"],
                total_credits=row["total_credits"],
                spent_credits=row["spent_credits"],
                reserved_credits=0
            )
            for row in rows
        ]

    async def commit_spent_credits(self, api_key: str, amount: int) -> bool:
        """Commit spent_credits (task succeeded). Returns False if user not found."""
        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET spent_credits = spent_credits + $1 WHERE api_key = $2",
                amount, api_key
            )
        success = result != "UPDATE 0"
        if success:
            logger.info(f"commit: spent_credits +{amount} for {api_key[:8]}")
        return success

    async def update_total_credits(self, api_key: str, total_credits: int) -> bool:
        """
        Set user's total_credits to a specific value (admin operation).

        Returns True if user was found and updated, False otherwise.
        """
        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET total_credits = $1 WHERE api_key = $2",
                total_credits, api_key
            )

        success = result != "UPDATE 0"
        if success:
            logger.info(f"Updated total_credits for api_key={api_key[:8]}... to {total_credits}")
        else:
            logger.warning(f"User not found: api_key={api_key[:8]}...")

        return success
