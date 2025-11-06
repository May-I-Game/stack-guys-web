from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import boto3
from botocore.config import Config
import anyio
import asyncio
import time
import uuid

# Config
REGION = "ap-northeast-2"
MATCHMAKING_CONFIG = "stack-guys-100player"
MM_TIMEOUT_SEC = 60

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

@app.get("/")
async def root():
    return {"message": "Stack Guys API Server"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/info")
async def info():
    return {"game": "Stack Guys", "version": "1.0.0", "server": "FastAPI"}

@app.post("/api/find-game")
async def find_game():
    """
    1) StartMatchmaking
    2) DescribeMatchmaking 폴링
    3) COMPLETED 시 내 PlayerId에 해당하는 PlayerSessionId 추출
    4) IP/Port/PlayerSessionId/GameSessionArn 반환
    """
    player_id = f"Player-{int(time.time())}"
    ticket_id = str(uuid.uuid4())

    try:
        # 1) 매치 시작
        await anyio.to_thread.run_sync(
            lambda: gamelift.start_matchmaking(
                ConfigurationName=MATCHMAKING_CONFIG,
                Players=[{"PlayerId": player_id}],
                TicketId=ticket_id,
            )
        )

        # 2) 매치 완료 대기
        start = time.time()
        while time.time() - start < MM_TIMEOUT_SEC:
            desc = await anyio.to_thread.run_sync(
                lambda: gamelift.describe_matchmaking(TicketIds=[ticket_id])
            )
            t = desc["TicketList"][0]
            status = t["Status"]

            if status == "COMPLETED":
                info = t.get("GameSessionConnectionInfo", {})
                ip = info.get("IpAddress")
                port = info.get("Port")
                arn = info.get("GameSessionArn")
                matched = info.get("MatchedPlayerSessions", [])

                # 내가 보낸 player_id와 매칭되는 세션 ID 선택
                my_ps = next((ps for ps in matched if ps.get("PlayerId") == player_id), None)

                if not (ip and port and arn and my_ps and my_ps.get("PlayerSessionId")):
                    raise HTTPException(
                        status_code=500,
                        detail="Match completed but connection info missing"
                    )

                return {
                    "success": True,
                    "server_ip": ip,
                    "server_port": port,
                    "player_session_id": my_ps["PlayerSessionId"],
                    "game_session_id": arn,
                }

            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                reason = t.get("StatusReason") or ""
                raise HTTPException(status_code=500, detail=f"Matchmaking failed: {status} {reason}")

            await asyncio.sleep(1)

        # 3) 타임아웃
        raise HTTPException(status_code=408, detail=f"Matchmaking timeout after {MM_TIMEOUT_SEC} seconds")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GameLift error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
