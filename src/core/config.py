from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

class Configs(BaseSettings):

    # File paramters
    FILE_TYPE: list[str] = ["application/pdf"]
    DATA_DIR: Path

    # read pdf files 
    CHUNK_SIZE: int = 65536
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

@lru_cache
def settings() -> BaseSettings: 
    return Configs()

