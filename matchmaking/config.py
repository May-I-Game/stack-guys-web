from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Game Server
    game_server_ip: str = "172.31.34.164"
    game_server_port_start: int = 7779
    game_server_port_end: int = 7790

    # Matchmaking
    max_players_per_session: int = 8
    matchmaking_timeout: int = 60

    class Config:
        env_file = ".env"


settings = Settings()
