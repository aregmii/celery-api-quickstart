import asyncio
from contextlib import asynccontextmanager
import asyncpg
from fastapi import FastAPI
from config import settings
from logger import get_logger
from routes import router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("Starting up...")
    
    # Create database connection pool with retry
    max_retries = 10
    for attempt in range(max_retries):
        try:
            app.state.db_pool = await asyncpg.create_pool(
                host=settings.database_host,
                port=settings.database_port,
                user=settings.database_user,
                password=settings.database_password,
                database=settings.database_name,
                min_size=settings.database_pool_min_size,
                max_size=settings.database_pool_max_size,
            )
            logger.info("Database pool created")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in 2s... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(2)
            else:
                logger.error(f"Failed to connect to database: {e}")
                raise
    
    yield
    
    await app.state.db_pool.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Task API",
    description="Async task processing with authentication and billing",
    lifespan=lifespan,
)

app.include_router(router)