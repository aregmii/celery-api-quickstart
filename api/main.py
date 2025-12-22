import asyncio
import uuid
import asyncpg
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings
from logger import configure_logging, get_logger, request_id_ctx
from routes import router

configure_logging()
logger = get_logger(__name__)

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request_id_ctx.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", message="Starting application...")
    try:
        app.state.db_pool = await asyncpg.create_pool(
            host=settings.database_host,
            port=settings.database_port,
            user=settings.database_user,
            password=settings.database_password,
            database=settings.database_name,
            min_size=1,
            max_size=10,
        )
    except Exception as e:
        logger.error("db_connection_failed", error=str(e))
        raise e
    
    yield
    
    await app.state.db_pool.close()
    logger.info("shutdown", message="Application shutdown complete")

app = FastAPI(title="Task API", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.include_router(router)