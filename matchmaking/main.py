from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uuid
from datetime import datetime
from models import (
    MatchmakingRequest,
    MatchmakingResponse,
    TicketStatusResponse,
    GameServerHeartbeat,
    GameServerInfo,
    MatchmakingStatus,
    GameServerStatus
)
from redis_client import RedisClient
from config import settings

app = FastAPI(title="Stack Guys Matchmaking Server", version="1.0.0")

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
    - 플레이어를 큐에 추가
    - 티켓 ID 반환
    """
    ticket_id = str(uuid.uuid4())
    player_id = request.player_id or str(uuid.uuid4())

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
        # SSH를 통해 게임 서버의 Unity 프로세스 강제 종료
        game_server_ip = "3.37.88.2"
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
    매칭 처리 로직
    - 큐에서 모든 대기자 처리
    - 사용 가능한 서버 찾기 (순차적으로 채우기)
    - 세션 할당
    """
    print("🔄 매칭 프로세스 시작...")

    # 큐가 빌 때까지 반복
    while True:
        queue_length = redis_client.get_queue_length()
        if queue_length == 0:
            print("✅ 매칭 프로세스 완료 (큐 비어있음)")
            return

        # 사용 가능한 서버 찾기
        available_servers = redis_client.get_available_servers()
        if not available_servers:
            print(f"⚠️ 사용 가능한 서버 없음 (대기 중인 플레이어: {queue_length}명)")
            return

        # 🔥 순차적으로 채우기: 가장 많이 사용 중인 서버 선택 (단, 가득 차지 않은 서버 중에서)
        # 포트 번호순으로 정렬해서 같은 인원이면 작은 포트 우선
        server = max(
            available_servers,
            key=lambda s: (int(s.get("current_players", 0)), -int(s.get("port", 0)))
        )

        # 큐에서 플레이어 꺼내기 (한 명씩)
        players = redis_client.get_from_queue(count=1)
        if not players:
            print("⚠️ 큐에서 플레이어를 가져올 수 없음")
            return

        for player_data in players:
            ticket_id = player_data["ticket_id"]
            player_id = player_data["player_id"]

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

            print(f"✅ Matched {player_id} to {server['ip']}:{server['port']} (현재 {server['current_players']}명)")

        # 서버 플레이어 수는 게임 서버의 하트비트에서 자동으로 업데이트됨 (NetworkManager.ConnectedClients.Count)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
