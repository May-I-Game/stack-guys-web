from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Redis
    redis_host: str = "master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com"
    redis_port: int = 6379
    redis_db: int = 0

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Matchmaking
    max_players_per_session: int = 8
    matchmaking_timeout: int = 60

    class Config:
        env_file = ".env"


settings = Settings()
