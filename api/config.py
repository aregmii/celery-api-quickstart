from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Pydantic automatically reads from environment variables
    matching the field names (case-insensitive).
    """
    
    # Database
    database_host: str = "postgres"
    database_port: int = 5432
    database_user: str = "postgres"
    database_password: str = "postgres"
    database_name: str = "postgres"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    
    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    
    # Cache
    cache_ttl_seconds: int = 15
    
    # App
    log_level: str = "INFO"
    
    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )
    
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()