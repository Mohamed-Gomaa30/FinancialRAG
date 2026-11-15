from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

class Configs(BaseSettings):

    # File paramters
    MAX_FILE_SIZE : int = Field(default=104857600, gt=0) #100MB
    FILE_TYPE: list[str] = ["application/pdf"]
    DATA_DIR: Path

    # File chunk
    CHUNK_SIZE: int = 65536

    # Postgres: job records (source of truth). "+psycopg" = use the psycopg 3 driver.
    # Host is "localhost" while the api runs on the host; "postgres" once it runs in compose.
    DATABASE_URL: str = "postgresql+psycopg://rag:rag@localhost:5432/rag"

    # Redis: the job queue (mailbox only, see PLAN.md section 2)
    REDIS_URL: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env also holds keys only docker-compose reads (POSTGRES_USER, ...).
        # Without this, pydantic-settings fails on them as "extra inputs".
        extra="ignore",
    )

@lru_cache
def settings() -> Configs:
    return Configs()
