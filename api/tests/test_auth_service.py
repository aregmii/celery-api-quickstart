"""
Unit tests for AuthService.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.auth_service import AuthService
from repositories.user_repository import UserRepository
from models.schemas import User
from exceptions import InvalidApiKeyError


class TestAuthService:
    """Tests for AuthService class."""

    @pytest.fixture
    def mock_user_repo(self, mock_db_pool) -> MagicMock:
        repo = MagicMock(spec=UserRepository)
        return repo

    @pytest.fixture
    def service(self, mock_redis, mock_user_repo) -> AuthService:
        return AuthService(mock_redis, mock_user_repo)

    @pytest.mark.asyncio
    async def test_get_user_by_api_key_cache_hit(self, service, mock_redis, mock_user):
        """Should return user from cache when present."""
        # Arrange
        mock_redis.get.return_value = mock_user.model_dump_json()

        # Act
        user = await service.get_user_by_api_key(mock_user.api_key)

        # Assert
        assert user.name == mock_user.name
        mock_redis.get.assert_called_once()

    
    @pytest.mark.asyncio
    async def test_get_user_by_api_key_cache_miss(self, service, mock_redis, mock_user_repo, mock_user):
        """Should query database and cache result on cache miss."""
        # Arrange
        mock_redis.get.return_value = None
        mock_user_repo.get_user_by_api_key = AsyncMock(return_value=mock_user)

        # Act
        user = await service.get_user_by_api_key(mock_user.api_key)

        # Assert
        assert user.name == mock_user.name
        mock_user_repo.get_user_by_api_key.assert_called_once()
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_by_api_key_invalid(self, service, mock_redis, mock_user_repo):
        """Should raise InvalidApiKeyError when user not found."""
        # Arrange
        mock_redis.get.return_value = None
        mock_user_repo.get_user_by_api_key = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(InvalidApiKeyError):
            await service.get_user_by_api_key("invalid-key")

    def test_invalidate_cache_by_api_key(self, service, mock_redis):
        """Should delete cache entry."""
        # Act
        service.invalidate_cache_by_api_key("550e8400-e29b-41d4-a716-446655440000")

        # Assert
        mock_redis.delete.assert_called_once_with("user:550e8400-e29b-41d4-a716-446655440000")