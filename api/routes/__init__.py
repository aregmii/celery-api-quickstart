from fastapi import APIRouter
from routes.health import router as health_router
from routes.tasks import router as tasks_router
from routes.admin import router as admin_router

router = APIRouter()

router.include_router(health_router, tags=["Health"])
router.include_router(tasks_router, tags=["Tasks"])
router.include_router(admin_router, prefix="/admin", tags=["Admin"])