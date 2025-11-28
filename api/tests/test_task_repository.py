"""
Unit tests for TaskRepository.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from repositories.task_repository import TaskRepository


class TestTaskRepository:
    """Tests for TaskRepository class."""

    @pytest.fixture
    def repo(self, mock_db_pool) -> TaskRepository:
        return TaskRepository(mock_db_pool)

    @pytest.mark.asyncio
    async def test_create_task(self, repo, mock_db_pool):
        """Should insert task into database."""
        # Arrange
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        await repo.create_task(
            task_id="abc-123",
            owner_api_key="550e8400-e29b-41d4-a716-446655440000",
            a=5,
            b=3
        )

        # Assert
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_task_by_id_found(self, repo, mock_db_pool):
        """Should return Task when found."""
        # Arrange
        mock_row = {
            "id": "abc-123",
            "owner_api_key": "550e8400-e29b-41d4-a716-446655440000",
            "a": 5,
            "b": 3,
            "status": "complete",
            "result": 8,
            "error_message": None,
            "created_at": None,
            "updated_at": None
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        task = await repo.get_task_by_id("abc-123")

        # Assert
        assert task is not None
        assert task.status == "complete"
        assert task.result == 8

    @pytest.mark.asyncio
    async def test_get_task_by_id_not_found(self, repo, mock_db_pool):
        """Should return None when task not found."""
        # Arrange
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Act
        task = await repo.get_task_by_id("nonexistent")

        # Assert
        assert task is None