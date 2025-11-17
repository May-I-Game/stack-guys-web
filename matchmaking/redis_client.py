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

    # ==================== Player Session Management (재입장) ====================

    def register_player_session(
        self,
        player_id: str,
        server_id: str,
        server_ip: str,
        server_port: int,
        session_id: str,
        character_type: str,
        character_name: str
    ) -> None:
        """플레이어 세션 등록 (게임 입장 시)"""
        data = {
            "player_id": player_id,
            "server_id": server_id,
            "server_ip": server_ip,
            "server_port": server_port,
            "session_id": session_id,
            "character_type": character_type,
            "character_name": character_name,
            "status": "CONNECTED",
            "joined_at": datetime.utcnow().isoformat(),
            "last_seen": datetime.utcnow().isoformat()
        }
        self.client.hset(f"player_session:{player_id}", mapping=data)
        self.client.expire(f"player_session:{player_id}", 1800)  # 30분 TTL

        # 플레이어 데이터 저장
        player_data = {
            "character_type": character_type,
            "character_name": character_name,
            "session_id": session_id
        }
        self.client.hset(f"player_data:{player_id}:{session_id}", mapping=player_data)
        self.client.expire(f"player_data:{player_id}:{session_id}", 1800)

        # 세션의 플레이어 목록에 추가
        self.client.sadd(f"session:{session_id}:players", player_id)

    def get_active_player_session(self, player_id: str) -> Optional[Dict]:
        """플레이어의 활성 세션 조회"""
        data = self.client.hgetall(f"player_session:{player_id}")
        if not data:
            return None

        # 세션이 여전히 유효한지 확인 (INGAME 서버만)
        server_id = data.get("server_id")
        if server_id:
            server_info = self.get_server_info(server_id)
            if server_info and server_info.get("status") == GameServerStatus.IN_GAME.value:
                return data

        # 세션이 유효하지 않으면 삭제 (INGAME이 아니거나 서버가 없음)
        self.client.delete(f"player_session:{player_id}")
        return None

    def get_player_data(self, player_id: str, session_id: str) -> Optional[Dict]:
        """플레이어 캐릭터 데이터 조회"""
        return self.client.hgetall(f"player_data:{player_id}:{session_id}")

    def update_player_status(self, player_id: str, status: str) -> None:
        """플레이어 상태 업데이트"""
        self.client.hset(f"player_session:{player_id}", mapping={
            "status": status,
            "last_seen": datetime.utcnow().isoformat()
        })

    def cleanup_player_session(self, player_id: str) -> None:
        """플레이어 세션 삭제"""
        session_data = self.client.hgetall(f"player_session:{player_id}")
        if session_data:
            session_id = session_data.get("session_id")
            if session_id:
                self.client.srem(f"session:{session_id}:players", player_id)
                self.client.delete(f"player_data:{player_id}:{session_id}")

        self.client.delete(f"player_session:{player_id}")

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

    # ==================== Atomic Server Assignment (Lua Script) ====================

    def atomic_assign_to_server(self) -> Optional[Dict]:
        """
        원자적으로 사용 가능한 서버를 선택하고 플레이어 수 증가

        Lua Script를 사용하여 다음 작업을 원자적으로 수행:
        1. 모든 서버 조회
        2. 사용 가능한 서버 필터링 (AVAILABLE/STARTING, 정원 미달, 하트비트 유효)
        3. 순차 채우기: 가장 많이 찬 서버 선택 (같으면 작은 포트 우선)
        4. 선택된 서버의 current_players 즉시 증가
        5. 서버 정보 반환

        이 방식으로 동시 요청 시 Race Condition 방지
        """
        lua_script = """
        local servers = redis.call('SMEMBERS', 'servers:all')
        local best_server = nil
        local best_count = -1
        local best_port = 999999
        local current_time_str = ARGV[1]

        -- ISO 시간을 초 단위로 변환하는 간단한 파싱 (정확하진 않지만 60초 체크용으로 충분)
        local function parse_iso_time(iso_str)
            if not iso_str then return 0 end
            -- 2025-11-16T16:39:01.787080 형식에서 초 추출 (간단히 문자열 비교)
            return iso_str
        end

        for _, server_id in ipairs(servers) do
            local server_key = 'server:' .. server_id
            local current_players = tonumber(redis.call('HGET', server_key, 'current_players') or '0')
            local max_players = tonumber(redis.call('HGET', server_key, 'max_players') or '100')
            local status = redis.call('HGET', server_key, 'status')
            local last_heartbeat = redis.call('HGET', server_key, 'last_heartbeat')
            local port = tonumber(redis.call('HGET', server_key, 'port') or '7779')

            -- 조건 체크: AVAILABLE 또는 STARTING, 정원 미달
            if (status == 'AVAILABLE' or status == 'STARTING') and
               current_players < max_players and
               last_heartbeat then

                -- 순차 채우기: 가장 많이 사용 중인 서버 선택
                -- current_players가 같으면 작은 포트 번호 우선
                if current_players > best_count or
                   (current_players == best_count and port < best_port) then
                    best_count = current_players
                    best_port = port
                    best_server = server_id
                end
            end
        end

        if best_server then
            -- 원자적으로 플레이어 수 증가
            redis.call('HINCRBY', 'server:' .. best_server, 'current_players', 1)
            return best_server
        else
            return nil
        end
        """

        try:
            # Lua 스크립트 실행 (원자적 연산!)
            result = self.client.eval(lua_script, 0, datetime.utcnow().isoformat())

            if result:
                server_id = result.decode('utf-8') if isinstance(result, bytes) else result
                server_info = self.get_server_info(server_id)
                return server_info
            return None
        except Exception as e:
            print(f"❌ Lua script error: {e}")
            return None

    # ==================== Health Check ====================

    def ping(self) -> bool:
        """Redis 연결 확인"""
        try:
            return self.client.ping()
        except:
            return False
