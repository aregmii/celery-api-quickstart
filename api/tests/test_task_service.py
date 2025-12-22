"""
Unit tests for TaskService.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.task_service import TaskService
from repositories.user_repository import UserRepository
from repositories.task_repository import TaskRepository
from services.auth_service import AuthService
from models.schemas import User, Task
from exceptions import InsufficientCreditsError, TaskNotFoundError


class TestTaskService:
    """Tests for TaskService class."""

    @pytest.fixture
    def mock_user_repo(self) -> MagicMock:
        return MagicMock(spec=UserRepository)

    @pytest.fixture
    def mock_task_repo(self) -> MagicMock:
        return MagicMock(spec=TaskRepository)

    @pytest.fixture
    def mock_auth_service(self) -> MagicMock:
        return MagicMock(spec=AuthService)

    @pytest.fixture
    def service(self, mock_user_repo, mock_task_repo, mock_auth_service) -> TaskService:
        return TaskService(mock_user_repo, mock_task_repo, mock_auth_service)

    @pytest.mark.asyncio
    @patch("worker.tasks.compute_task")
    async def test_submit_task_success(
        self, mock_celery, service, mock_user_repo, mock_task_repo, mock_user
    ):
        """Should create task and enqueue to Celery."""
        # Arrange
        mock_user_repo.deduct_credit_by_api_key = AsyncMock(return_value=99)
        mock_task_repo.create_task = AsyncMock()

        # Act
        task_id = await service.submit_task(mock_user, 5, 3)

        # Assert
        assert task_id is not None
        mock_user_repo.deduct_credit_by_api_key.assert_called_once()
        mock_task_repo.create_task.assert_called_once()
        mock_celery.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_task_insufficient_credits(
        self, service, mock_user_repo, mock_user
    ):
        """Should raise InsufficientCreditsError when no credits."""
        # Arrange
        mock_user_repo.deduct_credit_by_api_key = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(InsufficientCreditsError):
            await service.submit_task(mock_user, 5, 3)

    @pytest.mark.asyncio
    async def test_get_task_by_id_for_user_success(
        self, service, mock_task_repo, mock_user, mock_task
    ):
        """Should return task when found and owned by user."""
        # Arrange
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_task)

        # Act
        task = await service.get_task_by_id_for_user(mock_task.id, mock_user)

        # Assert
        assert task.id == mock_task.id

    @pytest.mark.asyncio
    async def test_get_task_by_id_for_user_not_found(
        self, service, mock_task_repo, mock_user
    ):
        """Should raise TaskNotFoundError when task not found."""
        # Arrange
        mock_task_repo.get_task_by_id = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(TaskNotFoundError):
            await service.get_task_by_id_for_user("nonexistent", mock_user)

    @pytest.mark.asyncio
    async def test_get_task_by_id_for_user_not_owner(
        self, service, mock_task_repo, mock_task
    ):
        """Should raise TaskNotFoundError when user doesn't own task."""
        # Arrange
        other_user = User(name="other", api_key="other-key", credits=100)
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_task)

        # Act & Assert
        with pytest.raises(TaskNotFoundError):
            await service.get_task_by_id_for_user(mock_task.id, other_user)