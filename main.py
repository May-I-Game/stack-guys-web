from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3
from botocore.config import Config
import anyio
import asyncio
import uuid
from typing import Optional

# =========================
# Config
# =========================
REGION = "ap-northeast-2"
MATCHMAKING_CONFIG = "stack-guys-100player"

# 클라이언트 폴링 권장 주기(클라가 이 간격으로 /api/ticket-status 호출)
POLL_SUGGESTED_INTERVAL_SEC = 3

# anyio의 스레드 워커 동시 실행 상한(동기 boto3 호출 폭주 방지)
THREADPOOL_LIMIT = anyio.CapacityLimiter(8)

boto_cfg = Config(
    region_name=REGION,
    retries={"max_attempts": 5, "mode": "standard"},
    read_timeout=10,
    connect_timeout=5,
)
gamelift = boto3.client("gamelift", config=boto_cfg)

app = FastAPI(title="Stack Guys API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Schemas
# =========================
class FindGameRequest(BaseModel):
    # 확장 여지: 매치 속성/지역/스킬 등
    attributes: Optional[dict] = None

class FindGameResponse(BaseModel):
    success: bool
    ticket_id: str
    player_id: str
    poll_interval_sec: int = POLL_SUGGESTED_INTERVAL_SEC

class TicketStatusResponse(BaseModel):
    status: str                      # QUEUED, SEARCHING, COMPLETED, FAILED, CANCELLED, TIMED_OUT ...
    success: bool = False
    retry_after_sec: int = POLL_SUGGESTED_INTERVAL_SEC
    # COMPLETED 시 아래 필드 채움
    server_ip: Optional[str] = None
    server_port: Optional[int] = None
    player_session_id: Optional[str] = None
    game_session_id: Optional[str] = None
    reason: Optional[str] = None

# =========================
# Health & Info
# =========================
@app.get("/")
async def root():
    return {"message": "Stack Guys API Server"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/info")
async def info():
    return {"game": "Stack Guys", "version": "1.0.0", "server": "FastAPI"}

# =========================
# Matchmaking: Start (즉시 티켓 반환)
# =========================
@app.post("/api/find-game", response_model=FindGameResponse)
async def find_game(body: FindGameRequest):
    """
    1) StartMatchmaking 호출만 수행하고 즉시 TicketId/PlayerId 반환
    2) 결과는 /api/ticket-status 로 폴링(또는 WebSocket/SSE로 전환 가능)
    """
    # 충돌 최소화를 위해 고유 PlayerId 사용
    player_id = f"p-{uuid.uuid4().hex}"
    ticket_id = str(uuid.uuid4())

    players = [{"PlayerId": player_id}]
    # 필요시 매치 규칙에서 참조할 속성 매핑
    if body.attributes:
        players[0]["PlayerAttributes"] = body.attributes

    try:
        # 동기 boto3 호출을 anyio 스레드 워커로 오프로딩 + 동시 실행 상한
        await anyio.to_thread.run_sync(
            lambda: gamelift.start_matchmaking(
                ConfigurationName=MATCHMAKING_CONFIG,
                Players=players,
                TicketId=ticket_id,
            ),
            limiter=THREADPOOL_LIMIT
        )

        return FindGameResponse(
            success=True,
            ticket_id=ticket_id,
            player_id=player_id,
        )

    except Exception as e:
        # 권한/리밋/파라미터 오류 등을 통일된 메시지로
        raise HTTPException(status_code=500, detail=f"StartMatchmaking error: {e}")

# =========================
# Matchmaking: Status (한 번 조회)
# =========================
@app.get("/api/ticket-status", response_model=TicketStatusResponse)
async def ticket_status(
    ticket_id: str = Query(..., description="StartMatchmaking에서 받은 TicketId"),
    player_id: str = Query(..., description="StartMatchmaking에서 사용한 PlayerId"),
):
    """
    티켓 상태를 '한 번' 조회해서 반환.
    - 클라이언트는 이 엔드포인트를 주기적으로 호출(권장 2~3초)
    - COMPLETED 시 접속 정보 반환
    """
    try:
        desc = await anyio.to_thread.run_sync(
            lambda: gamelift.describe_matchmaking(TicketIds=[ticket_id]),
            limiter=THREADPOOL_LIMIT
        )
        tickets = desc.get("TicketList") or []
        if not tickets:
            # 일시적 전파 지연/비정상 응답 대비
            return TicketStatusResponse(status="UNKNOWN", reason="Ticket not found yet")

        t = tickets[0]
        status = t.get("Status", "UNKNOWN")

        if status == "COMPLETED":
            info = t.get("GameSessionConnectionInfo") or {}
            ip = info.get("IpAddress")
            port = info.get("Port")
            arn = info.get("GameSessionArn")
            matched = info.get("MatchedPlayerSessions") or []
            my_ps = next((ps for ps in matched if ps.get("PlayerId") == player_id), None)
            if ip and port and arn and my_ps and my_ps.get("PlayerSessionId"):
                return TicketStatusResponse(
                    status=status,
                    success=True,
                    retry_after_sec=POLL_SUGGESTED_INTERVAL_SEC,
                    server_ip=ip,
                    server_port=port,
                    player_session_id=my_ps["PlayerSessionId"],
                    game_session_id=arn,
                )
            # 연결정보 전파 지연 케이스: 한두 번 더 폴링 유도
            return TicketStatusResponse(
                status=status,
                reason="Match completed but connection info not ready; retry",
            )

        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            return TicketStatusResponse(
                status=status,
                success=False,
                reason=t.get("StatusReason") or "",
            )

        # QUEUED/SEARCHING 등 진행 중
        return TicketStatusResponse(status=status)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DescribeMatchmaking error: {e}")

# =========================
# (선택) 취소 엔드포인트
# =========================
class CancelRequest(BaseModel):
    ticket_id: str

@app.post("/api/cancel-matchmaking")
async def cancel_matchmaking(body: CancelRequest):
    try:
        await anyio.to_thread.run_sync(
            lambda: gamelift.stop_matchmaking(TicketId=body.ticket_id),
            limiter=THREADPOOL_LIMIT
        )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"StopMatchmaking error: {e}")

# =========================
# Local run (dev)
# =========================
if __name__ == "__main__":
    import uvicorn
    # 개발용 단일 프로세스. 운영은 Gunicorn 권장(아래 2번 참고)
    uvicorn.run(app, host="0.0.0.0", port=8000)
