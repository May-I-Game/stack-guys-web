from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
from datetime import datetime


class MatchmakingStatus(str, Enum):
    QUEUED = "QUEUED"
    SEARCHING = "SEARCHING"
    MATCHED = "MATCHED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class GameServerStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    STARTING = "STARTING"
    IN_GAME = "IN_GAME"
    FULL = "FULL"
    OFFLINE = "OFFLINE"


class MatchmakingRequest(BaseModel):
    player_id: str


class MatchmakingResponse(BaseModel):
    success: bool
    ticket_id: str
    player_id: str
    status: MatchmakingStatus
    message: Optional[str] = None


class TicketStatusResponse(BaseModel):
    ticket_id: str
    player_id: str
    status: MatchmakingStatus
    server_ip: Optional[str] = None
    server_port: Optional[int] = None
    session_id: Optional[str] = None
    message: Optional[str] = None


class GameServerInfo(BaseModel):
    server_id: str
    ip: str
    port: int
    current_players: int
    max_players: int
    status: GameServerStatus
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    last_heartbeat: Optional[datetime] = None


class GameServerHeartbeat(BaseModel):
    server_id: str
    port: int
    current_players: int
    status: GameServerStatus
    cpu_usage: float
    memory_usage: float


class GameSessionInfo(BaseModel):
    session_id: str
    server_id: str
    current_players: int
    max_players: int
    status: GameServerStatus
    created_at: datetime
