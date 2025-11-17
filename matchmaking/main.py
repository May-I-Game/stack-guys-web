from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uuid
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from models import (
    MatchmakingRequest,
    MatchmakingResponse,
    TicketStatusResponse,
    GameServerHeartbeat,
    GameServerInfo,
    MatchmakingStatus,
    GameServerStatus,
    PlayerJoinedRequest,
    PlayerDataResponse
)
from redis_client import RedisClient
from config import settings

app = FastAPI(title="Stack Guys Matchmaking Server", version="1.0.0")

# 🔥 백그라운드 스케줄러 (자동 정리용)
scheduler = BackgroundScheduler()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis 클라이언트
redis_client = RedisClient()


# ==================== Auto Cleanup Function ====================

def auto_cleanup_dead_servers():
    """
    백그라운드에서 주기적으로 실행되는 죽은 서버 자동 정리 함수
    - 60초 이상 하트비트 없는 서버 삭제
    """
    try:
        timeout = 60  # 60초 타임아웃
        all_servers = redis_client.get_all_servers()
        current_time = datetime.utcnow()

        deleted_count = 0

        for server in all_servers:
            server_id = server.get("server_id")
            last_heartbeat_str = server.get("last_heartbeat")

            if not last_heartbeat_str:
                # 하트비트 없음 → 삭제
                redis_client.client.delete(f"server:{server_id}")
                redis_client.client.srem("servers:all", server_id)
                print(f"🗑️  [Auto-Cleanup] Deleted {server_id} (no_heartbeat)")
                deleted_count += 1
                continue

            try:
                last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
                time_diff = (current_time - last_heartbeat).total_seconds()

                if time_diff > timeout:
                    # 타임아웃 → 삭제
                    redis_client.client.delete(f"server:{server_id}")
                    redis_client.client.srem("servers:all", server_id)
                    print(f"🗑️  [Auto-Cleanup] Deleted {server_id} (timeout: {int(time_diff)}s)")
                    deleted_count += 1

            except Exception as e:
                # 파싱 실패 → 삭제
                redis_client.client.delete(f"server:{server_id}")
                redis_client.client.srem("servers:all", server_id)
                print(f"🗑️  [Auto-Cleanup] Deleted {server_id} (parse_error: {e})")
                deleted_count += 1

        if deleted_count > 0:
            print(f"✅ [Auto-Cleanup] Cleaned up {deleted_count} dead servers")

    except Exception as e:
        print(f"❌ [Auto-Cleanup] Error: {e}")


@app.on_event("startup")
async def startup_event():
    """서버 시작 시 초기화"""
    print(f"🚀 Matchmaking Server starting...")
    print(f"📡 Redis: {settings.redis_host}:{settings.redis_port}")

    # Redis 연결 확인
    if redis_client.ping():
        print("✅ Redis connected")
    else:
        print("❌ Redis connection failed")

    # 🔥 자동 정리 스케줄러 시작 (5분마다 실행)
    scheduler.add_job(auto_cleanup_dead_servers, 'interval', minutes=5, id='cleanup_dead_servers')
    scheduler.start()
    print("✅ Auto-cleanup scheduler started (runs every 5 minutes)")


@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 정리"""
    scheduler.shutdown()
    print("🛑 Auto-cleanup scheduler stopped")


@app.get("/")
async def root():
    """헬스 체크"""
    return {
        "status": "ok",
        "service": "Stack Guys Matchmaking Server",
        "version": "1.0.0",
        "redis_connected": redis_client.ping()
    }


@app.get("/health")
async def health_check():
    """상세 헬스 체크"""
    redis_status = redis_client.ping()
    queue_length = redis_client.get_queue_length()
    all_servers = redis_client.get_all_servers()
    available_servers = redis_client.get_available_servers()

    return {
        "status": "healthy" if redis_status else "unhealthy",
        "redis": "connected" if redis_status else "disconnected",
        "queue_length": queue_length,
        "total_servers": len(all_servers),
        "available_servers": len(available_servers),
        "timestamp": datetime.utcnow().isoformat()
    }


# ==================== Matchmaking APIs ====================

@app.post("/api/find-game", response_model=MatchmakingResponse)
async def find_game(request: MatchmakingRequest, background_tasks: BackgroundTasks):
    """
    매치메이킹 요청
    - 재입장 가능한 세션이 있으면 즉시 반환
    - 없으면 플레이어를 큐에 추가
    - 티켓 ID 반환
    """
    player_id = request.player_id or str(uuid.uuid4())

    # 재입장 체크
    active_session = redis_client.get_active_player_session(player_id)

    if active_session:
        # 세션이 여전히 유효한지 확인
        server_id = active_session.get("server_id")
        server = redis_client.get_server_info(server_id)

        if server and server.get("status") == GameServerStatus.IN_GAME.value:
            # 즉시 재입장 응답 (INGAME 서버만)
            ticket_id = str(uuid.uuid4())

            print(f"🔄 Player {player_id} rejoining INGAME server {server_id}")

            return MatchmakingResponse(
                success=True,
                ticket_id=ticket_id,
                player_id=player_id,
                status=MatchmakingStatus.MATCHED,
                server_ip=active_session["server_ip"],
                server_port=int(active_session["server_port"]),
                session_id=active_session["session_id"],
                is_rejoin=True,
                message="Rejoining previous game"
            )

    # 정상 매칭 프로세스
    ticket_id = str(uuid.uuid4())

    # 큐에 추가
    redis_client.add_to_queue(ticket_id, player_id)

    # 백그라운드에서 매칭 시도
    background_tasks.add_task(process_matchmaking)

    return MatchmakingResponse(
        success=True,
        ticket_id=ticket_id,
        player_id=player_id,
        status=MatchmakingStatus.QUEUED,
        message="Added to matchmaking queue"
    )


@app.get("/api/ticket-status", response_model=TicketStatusResponse)
async def get_ticket_status(ticket_id: str, player_id: str):
    """
    티켓 상태 조회
    - 매칭 진행 상황 확인
    """
    ticket_data = redis_client.get_ticket_status(ticket_id)

    if not ticket_data:
        raise HTTPException(status_code=404, detail="Ticket not found")

    status = MatchmakingStatus(ticket_data.get("status", "QUEUED"))

    return TicketStatusResponse(
        ticket_id=ticket_id,
        player_id=player_id,
        status=status,
        server_ip=ticket_data.get("server_ip"),
        server_port=int(ticket_data["server_port"]) if ticket_data.get("server_port") else None,
        session_id=ticket_data.get("session_id"),
        message=f"Status: {status.value}"
    )


# ==================== Game Server APIs ====================

@app.post("/api/server/heartbeat")
async def server_heartbeat(heartbeat: GameServerHeartbeat):
    """
    게임 서버 하트비트
    - Unity 게임 서버가 주기적으로 상태 보고
    """
    redis_client.update_server_heartbeat(
        server_id=heartbeat.server_id,
        port=heartbeat.port,
        current_players=heartbeat.current_players,
        status=heartbeat.status,
        cpu_usage=heartbeat.cpu_usage,
        memory_usage=heartbeat.memory_usage
    )

    return {"status": "ok", "message": "Heartbeat received"}


@app.post("/api/server/register")
async def register_server(server_info: GameServerInfo):
    """
    게임 서버 등록
    - 새 게임 서버가 시작될 때 호출
    """
    redis_client.register_server(server_info)

    return {"status": "ok", "message": "Server registered", "server_id": server_info.server_id}


@app.get("/api/servers")
async def get_servers():
    """
    모든 게임 서버 목록 조회
    """
    servers = redis_client.get_all_servers()
    return {"servers": servers, "count": len(servers)}


@app.get("/api/servers/available")
async def get_available_servers():
    """
    사용 가능한 게임 서버 목록 조회
    """
    servers = redis_client.get_available_servers()
    return {"servers": servers, "count": len(servers)}


@app.post("/api/servers/cleanup")
async def cleanup_dead_servers(timeout: int = 60):
    """
    죽은 게임 서버 정리
    - 하트비트가 timeout 초 이상 오래된 서버를 Redis에서 삭제
    """
    from datetime import datetime, timedelta

    all_servers = redis_client.get_all_servers()
    current_time = datetime.utcnow()

    alive_count = 0
    deleted_count = 0
    deleted_servers = []

    for server in all_servers:
        server_id = server.get("server_id")
        last_heartbeat_str = server.get("last_heartbeat")

        if not last_heartbeat_str:
            # 하트비트 없음 → 삭제
            redis_client.client.delete(f"server:{server_id}")
            redis_client.client.srem("servers:all", server_id)
            deleted_servers.append({"server_id": server_id, "reason": "no_heartbeat"})
            deleted_count += 1
            continue

        try:
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
            time_diff = (current_time - last_heartbeat).total_seconds()

            if time_diff > timeout:
                # 타임아웃 → 삭제
                redis_client.client.delete(f"server:{server_id}")
                redis_client.client.srem("servers:all", server_id)
                deleted_servers.append({
                    "server_id": server_id,
                    "reason": f"timeout_{int(time_diff)}s"
                })
                deleted_count += 1
            else:
                alive_count += 1

        except Exception as e:
            # 파싱 실패 → 삭제
            redis_client.client.delete(f"server:{server_id}")
            redis_client.client.srem("servers:all", server_id)
            deleted_servers.append({"server_id": server_id, "reason": f"parse_error: {str(e)}"})
            deleted_count += 1

    return {
        "status": "ok",
        "alive_servers": alive_count,
        "deleted_servers": deleted_count,
        "deleted_list": deleted_servers,
        "timeout_seconds": timeout
    }


@app.post("/api/servers/kill/{server_id}")
async def kill_specific_server(server_id: str, background_tasks: BackgroundTasks, delay: int = 0):
    """
    특정 게임 서버를 강제 종료

    Args:
        server_id: 종료할 서버 ID (예: game-server-3-37-88-2-7779)
        delay: 종료 전 대기 시간(초, 기본값: 0 - 즉시 종료)

    Example:
        POST /api/servers/kill/game-server-3-37-88-2-7779?delay=10
    """
    # Redis에서 서버 정보 조회
    server_info = redis_client.get_server_info(server_id)

    if not server_info:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found in Redis")

    port = server_info.get("port")
    ip = server_info.get("ip")

    if not port:
        raise HTTPException(status_code=400, detail="Server port information missing")

    print(f"🎯 서버 종료 요청: {server_id} (IP: {ip}, Port: {port}) - {delay}초 후 종료 예약")

    # 백그라운드 태스크로 종료 실행
    background_tasks.add_task(kill_server_after_delay, server_id, int(port), delay)

    return {
        "status": "ok",
        "message": f"Server {server_id} will be killed after {delay} seconds",
        "server_info": {
            "server_id": server_id,
            "ip": ip,
            "port": port,
            "current_players": server_info.get("current_players", 0)
        }
    }


@app.post("/api/player-joined")
async def player_joined(request: PlayerJoinedRequest):
    """
    게임 서버에서 플레이어 입장 시 호출
    - 플레이어 세션 데이터 생성
    - 캐릭터 정보 저장
    """
    server_info = redis_client.get_server_info(request.server_id)

    if not server_info:
        raise HTTPException(status_code=404, detail="Server not found")

    redis_client.register_player_session(
        player_id=request.player_id,
        server_id=request.server_id,
        server_ip=server_info["ip"],
        server_port=int(server_info["port"]),
        session_id=request.server_id,
        character_type=request.character_type,
        character_name=request.character_name
    )

    print(f"✅ Player joined: {request.player_id} ({request.character_name}) on server {request.server_id}")

    return {"status": "ok", "message": "Player session registered"}


@app.get("/api/player-data", response_model=PlayerDataResponse)
async def get_player_data(player_id: str, session_id: str):
    """
    Unity에서 재입장 시 플레이어 데이터 조회
    - 캐릭터 정보 반환
    """
    data = redis_client.get_player_data(player_id, session_id)

    if not data:
        raise HTTPException(status_code=404, detail="Player data not found")

    return PlayerDataResponse(
        player_id=player_id,
        character_type=data["character_type"],
        character_name=data["character_name"],
        session_id=session_id
    )


@app.post("/api/server/game-ended")
async def game_ended(data: dict, background_tasks: BackgroundTasks):
    """
    게임 종료 신호 수신 - 30초 대기 후 해당 서버 프로세스를 강제 종료
    """
    server_id = data.get("server_id")
    port = data.get("port")

    if not port:
        raise HTTPException(status_code=400, detail="Port is required")

    print(f"🎮 게임 종료 신호 수신: {server_id} (port: {port}) - 30초 후 프로세스 종료 예약")

    # 백그라운드 태스크로 30초 후 종료 실행
    background_tasks.add_task(kill_server_after_delay, server_id, port, delay=30)

    return {"status": "ok", "message": f"Server will be killed after 30 seconds for port {port}"}


async def kill_server_after_delay(server_id: str, port: int, delay: int):
    """
    지정된 시간(초) 대기 후 서버 프로세스 강제 종료
    """
    import asyncio
    import subprocess

    print(f"⏳ {delay}초 대기 시작: {server_id} (port: {port})")
    await asyncio.sleep(delay)

    print(f"🔴 {delay}초 경과 - 서버 프로세스 종료 시작: {server_id} (port: {port})")

    try:
        # 🔥 ASG 대응: Redis에서 서버 IP 조회
        server_info = redis_client.get_server_info(server_id)

        if not server_info:
            print(f"❌ 서버 정보 없음: {server_id} - Redis에서 이미 삭제됨")
            return

        game_server_ip = server_info.get("ip")
        if not game_server_ip:
            print(f"❌ 서버 IP 없음: {server_id}")
            return

        print(f"🎯 대상 서버: {game_server_ip}:{port}")

        # SSH를 통해 게임 서버의 Unity 프로세스 강제 종료
        ssh_key_path = "/home/ubuntu/unity-webgl-website-key-seoul.pem"

        cmd = f'ssh -i {ssh_key_path} -o StrictHostKeyChecking=no ubuntu@{game_server_ip} "sudo pkill -9 -f \\"Build_Server.x86_64.*-port {port}\\""'

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)

        print(f"✅ 서버 프로세스 종료 명령 실행: {game_server_ip}:{port}")
        print(f"   stdout: {result.stdout}")
        print(f"   stderr: {result.stderr}")
        print(f"   return_code: {result.returncode}")

        # Redis 데이터도 삭제
        redis_client.client.delete(f"server:{server_id}")

        print(f"✅ 서버 프로세스 종료 완료: {server_id} (port: {port})")
    except subprocess.TimeoutExpired:
        print(f"❌ SSH 타임아웃: {port}")
    except Exception as e:
        print(f"❌ 서버 종료 실패: {e}")


# ==================== Matchmaking Logic ====================

def process_matchmaking():
    """
    매칭 처리 로직 (원자적 서버 할당)
    - 큐에서 모든 대기자 처리
    - Lua Script로 원자적 서버 선택 및 인원 증가
    - 동시 요청 시 Race Condition 방지
    """
    print("🔄 매칭 프로세스 시작...")

    # 큐가 빌 때까지 반복
    while True:
        queue_length = redis_client.get_queue_length()
        if queue_length == 0:
            print("✅ 매칭 프로세스 완료 (큐 비어있음)")
            return

        # 큐에서 플레이어 꺼내기 (한 명씩)
        players = redis_client.get_from_queue(count=1)
        if not players:
            print("⚠️ 큐에서 플레이어를 가져올 수 없음")
            return

        for player_data in players:
            ticket_id = player_data["ticket_id"]
            player_id = player_data["player_id"]

            # 🔥 원자적으로 서버 선택 및 플레이어 수 증가 (Lua Script)
            # - 모든 서버 조회 → 필터링 → 순차 채우기 → current_players++를 원자적으로 실행
            # - 동시 요청이 와도 Redis 내부에서 직렬화되어 처리됨
            server = redis_client.atomic_assign_to_server()

            if not server:
                # 사용 가능한 서버 없음 → 다시 큐에 넣기
                print(f"⚠️ 사용 가능한 서버 없음 (대기: {queue_length}명) - 플레이어 {player_id} 재큐잉")
                redis_client.add_to_queue(ticket_id, player_id)
                return

            # 세션 ID 생성 (간단히 서버ID 사용)
            session_id = server["server_id"]

            # 티켓 상태 업데이트
            redis_client.set_ticket_status(
                ticket_id=ticket_id,
                player_id=player_id,
                status=MatchmakingStatus.MATCHED,
                server_ip=server["ip"],
                server_port=int(server["port"]),
                session_id=session_id
            )

            print(f"✅ Matched {player_id} to {server['ip']}:{server['port']} (할당 후 {int(server['current_players'])+1}명)")

        # 서버 플레이어 수는:
        # 1. 매칭 시점: Lua Script로 즉시 증가 (Redis의 current_players++)
        # 2. 주기적: 게임 서버 하트비트로 실제 값 동기화 (NetworkManager.ConnectedClients.Count)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
