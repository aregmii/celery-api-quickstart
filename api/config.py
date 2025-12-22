from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_host: str = "postgres"
    database_port: int = 5432
    database_user: str = "postgres"
    database_password: str = "postgres"
    database_name: str = "postgres"
    
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    
    log_level: str = "INFO"
    cache_ttl_seconds: int = 15

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

    @property
    def database_url_sync(self) -> str:
        return f"postgresql://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

settings = Settings()