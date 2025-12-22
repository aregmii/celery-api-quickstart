"""
Unit tests for UserRepository.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from repositories.user_repository import UserRepository
from models.schemas import User


class TestUserRepository:
    """Tests for UserRepository class."""

    @pytest.fixture
    def repo(self, mock_db_pool) -> UserRepository:
        return UserRepository(mock_db_pool)

    @pytest.mark.asyncio
    async def test_get_user_by_api_key_found(self, repo, mock_db_pool):
        """Should return User when found in database."""
        # Arrange
        mock_row = {
            "name": "test_user",
            "api_key": "550e8400-e29b-41d4-a716-446655440000",
            "credits": 100
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        user = await repo.get_user_by_api_key("550e8400-e29b-41d4-a716-446655440000")

        # Assert
        assert user is not None
        assert user.name == "test_user"
        assert user.credits == 100

    @pytest.mark.asyncio
    async def test_get_user_by_api_key_not_found(self, repo, mock_db_pool):
        """Should return None when user not found."""
        # Arrange
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        user = await repo.get_user_by_api_key("invalid-key")

        # Assert
        assert user is None

    @pytest.mark.asyncio
    async def test_deduct_credit_by_api_key_success(self, repo, mock_db_pool):
        """Should return remaining credits when deduction succeeds."""
        # Arrange
        mock_row = {"credits": 99}
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        remaining = await repo.deduct_credit_by_api_key("550e8400-e29b-41d4-a716-446655440000")

        # Assert
        assert remaining == 99

    @pytest.mark.asyncio
    async def test_deduct_credit_by_api_key_insufficient(self, repo, mock_db_pool):
        """Should return None when user has no credits."""
        # Arrange
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        remaining = await repo.deduct_credit_by_api_key("550e8400-e29b-41d4-a716-446655440000")

        # Assert
        assert remaining is None