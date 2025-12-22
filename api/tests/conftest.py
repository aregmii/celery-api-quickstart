"""
Pytest fixtures for unit tests.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from models.schemas import User, Task


@pytest.fixture
def mock_user() -> User:
    """Sample user for testing."""
    return User(
        name="test_user",
        api_key="550e8400-e29b-41d4-a716-446655440000",
        credits=100
    )


@pytest.fixture
def mock_admin_user() -> User:
    """Admin user for testing."""
    return User(
        name="admin",
        api_key="123e4567-e89b-12d3-a456-426614174000",
        credits=1000
    )


@pytest.fixture
def mock_task() -> Task:
    """Sample task for testing."""
    return Task(
        id="f4818528-dcf4-4b49-a9d3-1fcb6af896f6",
        owner_api_key="550e8400-e29b-41d4-a716-446655440000",
        a=5,
        b=3,
        status="complete",
        result=8
    )


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Mock database connection pool."""
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=AsyncMock())
    return pool


@pytest.fixture
def mock_redis() -> MagicMock:
    """Mock Redis client."""
    redis = MagicMock()
    redis.get = MagicMock(return_value=None)
    redis.setex = MagicMock()
    redis.delete = MagicMock()
    return redis