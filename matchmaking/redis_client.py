import redis
import json
from typing import Optional, List, Dict
from datetime import datetime
from config import settings
from models import GameServerInfo, GameServerStatus, MatchmakingStatus


class RedisClient:
    def __init__(self):
        self.client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True
        )

    # ==================== Matchmaking Queue ====================

    def add_to_queue(self, ticket_id: str, player_id: str) -> None:
        """매칭 큐에 플레이어 추가"""
        data = {
            "ticket_id": ticket_id,
            "player_id": player_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.client.lpush("matchmaking:queue", json.dumps(data))

        # 티켓 상태 저장
        self.set_ticket_status(ticket_id, player_id, MatchmakingStatus.QUEUED)

    def get_from_queue(self, count: int = 1) -> List[Dict]:
        """매칭 큐에서 플레이어 꺼내기"""
        players = []
        for _ in range(count):
            data = self.client.rpop("matchmaking:queue")
            if data:
                players.append(json.loads(data))
        return players

    def get_queue_length(self) -> int:
        """큐에 대기 중인 플레이어 수"""
        return self.client.llen("matchmaking:queue")

    # ==================== Ticket Management ====================

    def set_ticket_status(
        self,
        ticket_id: str,
        player_id: str,
        status: MatchmakingStatus,
        server_ip: Optional[str] = None,
        server_port: Optional[int] = None,
        session_id: Optional[str] = None
    ) -> None:
        """티켓 상태 설정"""
        data = {
            "ticket_id": ticket_id,
            "player_id": player_id,
            "status": status.value,
            "updated_at": datetime.utcnow().isoformat()
        }

        # None이 아닌 값만 추가 (Redis는 None을 저장할 수 없음)
        if server_ip is not None:
            data["server_ip"] = server_ip
        if server_port is not None:
            data["server_port"] = server_port
        if session_id is not None:
            data["session_id"] = session_id

        self.client.hset(f"ticket:{ticket_id}", mapping=data)
        self.client.expire(f"ticket:{ticket_id}", 300)  # 5분 후 자동 삭제

    def get_ticket_status(self, ticket_id: str) -> Optional[Dict]:
        """티켓 상태 조회"""
        data = self.client.hgetall(f"ticket:{ticket_id}")
        return data if data else None

    # ==================== Game Server Management ====================

    def register_server(self, server_info: GameServerInfo) -> None:
        """게임 서버 등록"""
        data = {
            "server_id": server_info.server_id,
            "ip": server_info.ip,
            "port": server_info.port,
            "current_players": server_info.current_players,
            "max_players": server_info.max_players,
            "status": server_info.status.value,
            "cpu_usage": server_info.cpu_usage or 0.0,
            "memory_usage": server_info.memory_usage or 0.0,
            "last_heartbeat": datetime.utcnow().isoformat()
        }
        self.client.hset(f"server:{server_info.server_id}", mapping=data)
        self.client.sadd("servers:all", server_info.server_id)

    def update_server_heartbeat(
        self,
        server_id: str,
        port: int,
        current_players: int,
        status: GameServerStatus,
        cpu_usage: float,
        memory_usage: float
    ) -> None:
        """서버 하트비트 업데이트"""
        self.client.hset(f"server:{server_id}", mapping={
            "port": port,
            "current_players": current_players,
            "status": status.value,
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "last_heartbeat": datetime.utcnow().isoformat()
        })

    def get_server_info(self, server_id: str) -> Optional[Dict]:
        """서버 정보 조회"""
        return self.client.hgetall(f"server:{server_id}")

    def get_all_servers(self) -> List[Dict]:
        """모든 서버 목록"""
        server_ids = self.client.smembers("servers:all")
        servers = []
        for server_id in server_ids:
            server_data = self.get_server_info(server_id)
            if server_data:
                servers.append(server_data)
        return servers

    def get_available_servers(self) -> List[Dict]:
        """사용 가능한 서버 목록 (AVAILABLE 또는 STARTING 상태, 하트비트가 살아있는 서버만)"""
        all_servers = self.get_all_servers()
        available = []
        current_time = datetime.utcnow()

        for s in all_servers:
            # AVAILABLE 또는 STARTING 상태만
            if s.get("status") not in [GameServerStatus.AVAILABLE.value, GameServerStatus.STARTING.value]:
                continue

            # 정원이 차지 않은 서버만
            if int(s.get("current_players", 0)) >= int(s.get("max_players", 8)):
                continue

            # 하트비트 타임아웃 체크 (60초)
            last_heartbeat_str = s.get("last_heartbeat")
            if last_heartbeat_str:
                try:
                    last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
                    time_diff = (current_time - last_heartbeat).total_seconds()
                    if time_diff > 60:  # 60초 이상 하트비트 없으면 제외
                        print(f"⚠️ Server {s.get('server_id')} heartbeat timeout ({time_diff:.0f}s)")
                        continue
                except:
                    pass  # 파싱 실패 시 일단 포함

            available.append(s)

        return available

    # ==================== Game Session Management ====================

    def create_session(self, session_id: str, server_id: str, max_players: int) -> None:
        """게임 세션 생성"""
        data = {
            "session_id": session_id,
            "server_id": server_id,
            "current_players": 0,
            "max_players": max_players,
            "status": GameServerStatus.AVAILABLE.value,
            "created_at": datetime.utcnow().isoformat()
        }
        self.client.hset(f"session:{session_id}", mapping=data)
        self.client.sadd("sessions:active", session_id)

    def add_player_to_session(self, session_id: str, player_id: str) -> None:
        """세션에 플레이어 추가"""
        self.client.sadd(f"session:{session_id}:players", player_id)
        self.client.hincrby(f"session:{session_id}", "current_players", 1)

    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """세션 정보 조회"""
        return self.client.hgetall(f"session:{session_id}")

    def get_session_players(self, session_id: str) -> List[str]:
        """세션의 플레이어 목록"""
        return list(self.client.smembers(f"session:{session_id}:players"))

    def close_session(self, session_id: str) -> None:
        """세션 종료"""
        self.client.srem("sessions:active", session_id)
        self.client.delete(f"session:{session_id}")
        self.client.delete(f"session:{session_id}:players")

    # ==================== Health Check ====================

    def ping(self) -> bool:
        """Redis 연결 확인"""
        try:
            return self.client.ping()
        except:
            return False
