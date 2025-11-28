# Stack Guys 포스트세션 Q&A

Unity WebGL 멀티플레이어 게임 인프라 기술 질문 대비 문서

---

## 📋 목차

1. [전체 아키텍처](#1-전체-아키텍처)
2. [AWS 인프라](#2-aws-인프라)
3. [매치메이킹 서버](#3-매치메이킹-서버)
4. [게임 서버](#4-게임-서버)
5. [Redis & 데이터 관리](#5-redis--데이터-관리)
6. [네트워크 & 보안](#6-네트워크--보안)
7. [성능 & 확장성](#7-성능--확장성)
8. [테스트 & 모니터링](#8-테스트--모니터링)
9. [트러블슈팅](#9-트러블슈팅)

---

## 1. 전체 아키텍처

### Q1-1. 전체 서버 구조를 설명해주세요.

**A:**
```
WebGL Client (Browser)
    ↓ HTTP
ALB (stackguys-alb)
    ↓
EC2 (Nginx + FastAPI)
    ├─ Nginx (Port 80): Unity WebGL 정적 파일 서빙
    └─ FastAPI (Port 8000): 매치메이킹 API
        ↓
ElastiCache Redis (TLS/SSL)
    ↑
Unity Game Server (EC2, Ports 7779-7790)
```

**주요 흐름:**
1. **WebGL 클라이언트** → ALB → Nginx → Unity WebGL 파일 다운로드
2. **매치메이킹 요청** → ALB → FastAPI → Redis (큐 추가)
3. **매칭 처리** → FastAPI Background Task → Lua Script (원자적 서버 할당)
4. **게임 접속** → WebGL Client → Unity Game Server (WebSocket)
5. **하트비트** → Unity Game Server → FastAPI → Redis (서버 상태 갱신)

---

### Q1-2. 왜 이런 구조를 선택했나요?

**A:**

**1) ALB를 사용한 이유:**
- EC2 인스턴스 장애 시 자동 트래픽 분산
- Auto Scaling Group과 연동하여 수평 확장 가능
- Health Check로 비정상 인스턴스 자동 제거

**2) Nginx + FastAPI 분리:**
- Nginx: 정적 파일(WebGL) 서빙 최적화 (Brotli 압축)
- FastAPI: 동적 API 처리 (매치메이킹, 서버 관리)
- 역할 분리로 성능 최적화

**3) ElastiCache Redis 사용:**
- 여러 매치메이킹 서버가 상태 공유 (분산 환경)
- TLS/SSL 암호화로 보안 강화
- 자동 백업 및 Multi-AZ 고가용성

**4) Unity Dedicated Server 분리:**
- 게임 로직을 별도 EC2에서 실행
- 포트별로 여러 게임 세션 동시 운영 (7779, 7780, ...)
- ASG로 게임 서버 자동 확장

---

### Q1-3. GameLift 대신 직접 구축한 이유는?

**A:**

**GameLift를 사용하지 않은 이유:**
- 비용: GameLift 인스턴스는 EC2 대비 비용이 높음
- 유연성: 직접 구축으로 커스터마이징 자유도 확보
- 학습: 인프라 구조에 대한 깊은 이해 필요

**직접 구축의 장점:**
- EC2 + ASG로 비용 최적화
- FastAPI + Redis로 매치메이킹 로직 완전 제어
- systemd로 게임 서버 프로세스 관리 가능
- SSH를 통한 원격 프로세스 제어 (게임 종료 시 자동 kill)

**직접 구축의 단점:**
- GameLift의 자동 스케일링/플릿 관리 기능 없음
- 직접 하트비트, 서버 등록, 정리 로직 구현 필요
- CloudWatch 커스텀 메트릭 수동 설정

---

## 2. AWS 인프라

### Q2-1. ALB 구성을 설명해주세요.

**A:**

**1) WebGL 서빙용 ALB (stackguys-alb)**
- **리스너**: HTTP 80 포트
- **Target Group**: 웹 서버 EC2 (Nginx)
- **Health Check**: `/health` 엔드포인트
- **역할**: Unity WebGL 클라이언트 제공

**2) 매치메이킹용 ALB (matchmaking-alb)**
- **도메인**: `matchmaking-alb-1609632759.ap-northeast-2.elb.amazonaws.com`
- **리스너**: HTTP 80 포트
- **Target Group**: 매치메이킹 서버 EC2 (FastAPI)
- **Health Check**: `/health` 엔드포인트 (Redis 연결 상태 포함)
- **역할**: 매치메이킹 API 제공

**Health Check 응답 예시:**
```json
{
  "status": "healthy",
  "redis": "connected",
  "queue_length": 0,
  "total_servers": 2,
  "available_servers": 2,
  "timestamp": "2025-11-28T10:00:00.000000"
}
```

---

### Q2-2. Auto Scaling Group 구성은?

**A:**

**1) 게임 서버 ASG (stack-guys-game-server-asg2)**
- **AMI**: 게임 서버 EC2로부터 생성한 이미지
- **인스턴스 타입**: c5.large (CPU 최적화)
- **용량**: 최소 1 / 원하는 1 / 최대 5
- **스케일링 정책**: CPU 기반 Target Tracking (70%)
- **포함 내용**:
  - Unity Dedicated Server 빌드 파일
  - systemd 서비스 (game-server@7779.service, game-server@7780.service)
  - SSL 인증서 (자체 서명 인증서, WSS 지원)

**2) 매치메이킹 서버 ASG (계획 중)**
- **AMI**: 매치메이킹 서버 EC2로부터 생성 예정
- **인스턴스 타입**: t3.medium
- **용량**: 최소 2 / 원하는 2 / 최대 10
- **스케일링 정책**: ALBRequestCountPerTarget 기반 (150 req/inst)
- **포함 내용**:
  - FastAPI 앱 (/opt/matchmaking)
  - Python venv 환경
  - systemd 서비스 (matchmaking.service)

---

### Q2-3. ElastiCache Redis 구성은?

**A:**

**엔드포인트:**
- Primary: `master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com`
- Replica: `replica.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com` (읽기 전용)

**사양:**
- **노드 타입**: cache.r7g.large (2 vCPU, 13.07GB RAM)
- **클러스터 모드**: Disabled (단순 Primary-Replica 구조)
- **Multi-AZ**: 활성화 (ap-northeast-2a, 2b)
- **TLS/SSL**: 활성화 (전송 중 암호화)
- **버전**: Redis 7.1.0

**보안 그룹:**
- 매치메이킹 서버 SG만 접근 허용 (6379 포트)
- 게임 서버 SG 접근 허용 (6379 포트)
- 외부 접근 차단

**연결 설정 (Python):**
```python
redis.Redis(
    host='master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com',
    port=6379,
    ssl=True,
    ssl_cert_reqs=None,  # 인증서 검증 비활성화
    ssl_check_hostname=False,
    decode_responses=True,
    socket_connect_timeout=10,
    socket_timeout=10
)
```

---

### Q2-4. Security Group 구성은?

**A:**

**1) unity-website-sg (웹 서버용)**
- **Inbound:**
  - HTTP (80) ← 0.0.0.0/0 (모든 사용자)
  - SSH (22) ← 내 IP (관리용)
  - FastAPI (8000) ← ALB SG (내부 통신)
- **Outbound:** 모든 트래픽 허용

**2) matchmaking-server-sg (매치메이킹 서버용)**
- **Inbound:**
  - FastAPI (8000) ← ALB SG
  - SSH (22) ← 내 IP
- **Outbound:**
  - Redis (6379) → ElastiCache SG
  - HTTPS (443) → 0.0.0.0/0 (외부 API 호출용)

**3) game-server-sg (게임 서버용)**
- **Inbound:**
  - WebSocket (7779-7790) ← 0.0.0.0/0 (게임 클라이언트)
  - WebSocket TLS (8779-8790) ← 0.0.0.0/0 (WSS)
  - SSH (22) ← 매치메이킹 서버 SG (원격 프로세스 제어)
- **Outbound:**
  - Redis (6379) → ElastiCache SG
  - HTTPS (443) → 매치메이킹 ALB (하트비트)

**4) elasticache-redis-sg (Redis용)**
- **Inbound:**
  - Redis (6379) ← matchmaking-server-sg
  - Redis (6379) ← game-server-sg
- **Outbound:** 없음

---

## 3. 매치메이킹 서버

### Q3-1. FastAPI 매치메이킹 서버의 핵심 기능은?

**A:**

**주요 엔드포인트:**

**1) 매치메이킹 API**
```python
# POST /api/find-game
# 매칭 요청 → 티켓 발급 → 대기열 추가
{
  "player_id": "uuid"
}
→ Response:
{
  "success": true,
  "ticket_id": "abc123",
  "status": "QUEUED"
}

# GET /api/ticket-status?ticket_id=abc123&player_id=uuid
# 티켓 상태 조회 (폴링)
→ Response:
{
  "status": "MATCHED",
  "server_ip": "3.37.88.2",
  "server_port": 7779,
  "session_id": "session-xyz"
}
```

**2) 게임 서버 관리 API**
```python
# POST /api/server/register
# 게임 서버 등록
{
  "server_id": "game-server-3-37-88-2-7779",
  "ip": "3.37.88.2",
  "port": 7779,
  "max_players": 100
}

# POST /api/server/heartbeat
# 하트비트 (30초마다)
{
  "server_id": "game-server-3-37-88-2-7779",
  "current_players": 45,
  "status": "IN_GAME",
  "cpu_usage": 52.3,
  "memory_usage": 68.1
}

# POST /api/server/game-ended
# 게임 종료 신호 → 30초 후 프로세스 종료
{
  "server_id": "game-server-3-37-88-2-7779",
  "port": 7779
}
```

**3) 플레이어 세션 API (재입장 지원)**
```python
# POST /api/player-joined
# 플레이어 입장 알림
{
  "player_id": "uuid",
  "server_id": "game-server-3-37-88-2-7779",
  "character_type": "warrior",
  "character_name": "Hero123"
}

# GET /api/player-data?player_id=uuid&session_id=xyz
# 재입장 시 캐릭터 정보 조회
```

---

### Q3-2. Lua Script 원자적 서버 할당을 설명해주세요.

**A:**

**문제:**
- 여러 매치메이킹 서버가 동시에 같은 게임 서버에 플레이어를 배정하면?
- Race Condition 발생 → 서버 정원 초과 가능

**해결책: Lua Script 원자적 연산**

**왜 Lua Script인가?**
- Redis는 단일 스레드 → Lua Script는 원자적으로 실행
- 읽기 + 쓰기를 하나의 트랜잭션으로 처리
- 동시 요청이 와도 직렬화되어 처리

**동작 방식:**
```lua
-- redis_client.py의 atomic_assign_to_server()

lua_script = """
1. servers:all 조회 (모든 게임 서버 ID)
2. 각 서버 순회:
   - current_players, max_players, status 읽기
   - 조건 체크: AVAILABLE/STARTING + 정원 미달
3. 순차 채우기 전략:
   - 가장 많이 찬 서버 선택 (빈 서버 최소화)
   - current_players 같으면 작은 포트 우선
4. HINCRBY로 current_players +1 (원자적!)
5. 서버 ID 반환
"""

result = redis.eval(lua_script, 0, datetime.utcnow().isoformat())
```

**예시 시나리오:**
```
초기 상태:
Server A: 10명/100명
Server B: 5명/100명

동시에 3명이 매칭 요청:
  매칭서버#1 → Player 1
  매칭서버#2 → Player 2
  매칭서버#3 → Player 3

Lua Script 실행 (원자적!):
  #1: Server A (10명) 선택 → 10→11
  #2: Server A (11명) 선택 → 11→12  # 이미 증가됨!
  #3: Server A (12명) 선택 → 12→13

결과:
Server A: 13명/100명 ✅ 정확히 3명 증가
Server B: 5명/100명
```

**장점:**
- Race Condition 완벽 차단
- 순차 채우기로 빈 서버 최소화 (비용 절감)
- ASG로 매치메이킹 서버가 10개로 늘어도 문제 없음

---

### Q3-3. 백그라운드 매칭 워커는 어떻게 동작하나요?

**A:**

**process_matchmaking() 함수:**
```python
# main.py

def process_matchmaking():
    """
    백그라운드에서 무한 루프로 대기열 처리
    """
    while True:
        queue_length = redis_client.get_queue_length()
        if queue_length == 0:
            return  # 큐 비어있음

        # 큐에서 플레이어 1명씩 꺼내기
        players = redis_client.get_from_queue(count=1)

        for player_data in players:
            ticket_id = player_data["ticket_id"]
            player_id = player_data["player_id"]

            # 🔥 원자적으로 서버 선택 및 플레이어 수 증가
            server = redis_client.atomic_assign_to_server()

            if not server:
                # 사용 가능한 서버 없음 → 재큐잉
                redis_client.add_to_queue(ticket_id, player_id)
                return

            # 티켓 상태 업데이트: QUEUED → MATCHED
            redis_client.set_ticket_status(
                ticket_id=ticket_id,
                player_id=player_id,
                status=MatchmakingStatus.MATCHED,
                server_ip=server["ip"],
                server_port=int(server["port"]),
                session_id=server["server_id"]
            )
```

**호출 방식:**
```python
@app.post("/api/find-game")
async def find_game(request: MatchmakingRequest, background_tasks: BackgroundTasks):
    # 큐에 추가
    redis_client.add_to_queue(ticket_id, player_id)

    # 백그라운드 태스크로 매칭 시도
    background_tasks.add_task(process_matchmaking)
```

**특징:**
- 비동기 백그라운드 실행 (FastAPI BackgroundTasks)
- 큐가 빌 때까지 반복
- 서버 없으면 재큐잉 (나중에 다시 시도)

---

### Q3-4. 죽은 서버 자동 정리는 어떻게 하나요?

**A:**

**APScheduler 사용:**
```python
# main.py

from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

@app.on_event("startup")
async def startup_event():
    # 5분마다 실행
    scheduler.add_job(
        auto_cleanup_dead_servers,
        'interval',
        minutes=5,
        id='cleanup_dead_servers'
    )
    scheduler.start()

def auto_cleanup_dead_servers():
    """
    하트비트가 60초 이상 없는 서버 제거
    """
    timeout = 60  # 60초 타임아웃
    all_servers = redis_client.get_all_servers()
    current_time = datetime.utcnow()

    for server in all_servers:
        server_id = server.get("server_id")
        last_heartbeat_str = server.get("last_heartbeat")

        if not last_heartbeat_str:
            # 하트비트 없음 → 삭제
            redis_client.client.delete(f"server:{server_id}")
            redis_client.client.srem("servers:all", server_id)
            continue

        last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
        time_diff = (current_time - last_heartbeat).total_seconds()

        if time_diff > timeout:
            # 타임아웃 → 삭제
            redis_client.client.delete(f"server:{server_id}")
            redis_client.client.srem("servers:all", server_id)
```

**작동 방식:**
1. 5분마다 자동 실행
2. 모든 서버의 `last_heartbeat` 확인
3. 60초 초과 → Redis에서 삭제
4. 매칭 시 자동으로 제외됨

---

## 4. 게임 서버

### Q4-1. Unity 게임 서버는 어떻게 실행되나요?

**A:**

**systemd 서비스로 관리:**

**파일:** `/etc/systemd/system/game-server@.service`
```ini
[Unit]
Description=Stack Guys Game Server (Port %i)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/game_server
ExecStart=/home/ubuntu/game_server/Build_Server.x86_64 -batchmode -nographics -port %i
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**사용법:**
```bash
# 7779 포트로 게임 서버 시작
sudo systemctl start game-server@7779

# 7780 포트로 게임 서버 시작
sudo systemctl start game-server@7780

# 상태 확인
sudo systemctl status game-server@7779

# 로그 확인
sudo journalctl -u game-server@7779 -f
```

**특징:**
- `%i`는 `@` 뒤의 숫자로 치환 (포트 번호)
- 자동 재시작 (Restart=on-failure)
- 부팅 시 자동 시작 (enable)

---

### Q4-2. 게임 서버는 어떻게 자신을 등록하나요?

**A:**

**Unity C# 코드 (NetworkGameManager.cs):**
```csharp
void Start()
{
    // EC2 메타데이터에서 Public IP 자동 감지
    string publicIP = GetEC2PublicIP();

    // 매치메이킹 서버에 등록
    StartCoroutine(RegisterServer(publicIP, serverPort));

    // 30초마다 하트비트 전송
    InvokeRepeating("SendHeartbeat", 30f, 30f);
}

IEnumerator RegisterServer(string ip, int port)
{
    var data = new {
        server_id = $"game-server-{ip.Replace(".", "-")}-{port}",
        ip = ip,
        port = port,
        max_players = 100,
        status = "AVAILABLE"
    };

    UnityWebRequest request = UnityWebRequest.Post(
        "http://matchmaking-alb.../api/server/register",
        JsonUtility.ToJson(data)
    );

    yield return request.SendWebRequest();
}

void SendHeartbeat()
{
    var data = new {
        server_id = serverID,
        port = serverPort,
        current_players = NetworkManager.Singleton.ConnectedClients.Count,
        status = gameState,  // AVAILABLE, STARTING, IN_GAME
        cpu_usage = GetCPUUsage(),
        memory_usage = GetMemoryUsage()
    };

    // POST /api/server/heartbeat
}
```

**EC2 Public IP 자동 감지:**
```csharp
string GetEC2PublicIP()
{
    try
    {
        // EC2 메타데이터 API
        string url = "http://169.254.169.254/latest/meta-data/public-ipv4";
        UnityWebRequest request = UnityWebRequest.Get(url);
        request.timeout = 5;

        var op = request.SendWebRequest();
        while (!op.isDone) { }

        if (request.result == UnityWebRequest.Result.Success)
        {
            return request.downloadHandler.text;
        }
    }
    catch { }

    return "127.0.0.1";  // 로컬 테스트용
}
```

---

### Q4-3. 게임 종료 후 프로세스는 어떻게 정리되나요?

**A:**

**1) 게임 서버에서 종료 신호 전송:**
```csharp
// Unity NetworkGameManager.cs

void OnGameEnd()
{
    // 매치메이킹 서버에 게임 종료 알림
    StartCoroutine(NotifyGameEnd());
}

IEnumerator NotifyGameEnd()
{
    var data = new {
        server_id = serverID,
        port = serverPort
    };

    UnityWebRequest request = UnityWebRequest.Post(
        "http://matchmaking-alb.../api/server/game-ended",
        JsonUtility.ToJson(data)
    );

    yield return request.SendWebRequest();
}
```

**2) 매치메이킹 서버에서 30초 후 SSH로 프로세스 종료:**
```python
# main.py

@app.post("/api/server/game-ended")
async def game_ended(data: dict, background_tasks: BackgroundTasks):
    server_id = data.get("server_id")
    port = data.get("port")

    # 30초 대기 후 종료 예약
    background_tasks.add_task(kill_server_after_delay, server_id, port, delay=30)

    return {"status": "ok", "message": "Server will be killed after 30 seconds"}

async def kill_server_after_delay(server_id: str, port: int, delay: int):
    await asyncio.sleep(delay)  # 30초 대기

    # Redis에서 서버 IP 조회
    server_info = redis_client.get_server_info(server_id)
    game_server_ip = server_info.get("ip")

    # SSH로 Unity 프로세스 종료
    ssh_key_path = "/home/ubuntu/unity-webgl-website-key-seoul.pem"
    cmd = f'ssh -i {ssh_key_path} ubuntu@{game_server_ip} "sudo pkill -9 -f \\"Build_Server.x86_64.*-port {port}\\""'

    subprocess.run(cmd, shell=True, timeout=10)

    # Redis에서 서버 정보 삭제
    redis_client.client.delete(f"server:{server_id}")
```

**왜 SSH를 사용하나?**
- ASG로 생성된 게임 서버는 동적 IP
- 매치메이킹 서버가 게임 서버의 IP를 Redis에서 조회
- SSH로 원격 명령 실행 (pkill)

**왜 30초 대기?**
- 플레이어가 결과 화면을 볼 시간 확보
- 네트워크 지연으로 인한 연결 종료 대비

---

### Q4-4. WebSocket vs WebSocket Secure (WSS)?

**A:**

**현재 설정:**
- Unity 게임 서버는 **WSS (WebSocket Secure)** 지원
- 포트: 7779 (WS), 8779 (WSS)
- SSL 인증서: 자체 서명 인증서

**Unity 서버 코드:**
```csharp
// NetworkGameManager.cs

void Start()
{
    var transport = NetworkManager.Singleton.GetComponent<UnityTransport>();

    // SSL 인증서 설정
    byte[] serverCert = Resources.Load<TextAsset>("server_cert").bytes;
    byte[] serverKey = Resources.Load<TextAsset>("server_key").bytes;

    transport.SetServerSecrets(serverCert, serverKey);

    // WebSocket 모드 활성화
    transport.UseWebSockets = true;
    transport.Port = (ushort)(serverPort + 1000);  // 7779 → 8779

    NetworkManager.Singleton.StartServer();
}
```

**WebGL 클라이언트 연결:**
```javascript
// index.html

const serverIP = "3.37.88.2";
const serverPort = 7779;

// WSS 연결 (포트 +1000)
const wsURL = `wss://${serverIP}:${serverPort + 1000}/`;
```

**문제점:**
- 자체 서명 인증서 → 브라우저 경고
- 프로덕션에서는 Let's Encrypt 등 공인 인증서 필요

---

## 5. Redis & 데이터 관리

### Q5-1. Redis 데이터 구조를 설명해주세요.

**A:**

```
1️⃣ 게임 서버 관리
servers:all                        (Set)    서버 ID 목록
  └─ "game-server-3-37-88-2-7779"
  └─ "game-server-3-37-88-2-7780"

server:{server_id}                 (Hash)   서버 상세 정보
  ├─ server_id: "game-server-3-37-88-2-7779"
  ├─ ip: "3.37.88.2"
  ├─ port: "7779"
  ├─ current_players: "45"
  ├─ max_players: "100"
  ├─ status: "IN_GAME"
  ├─ cpu_usage: "52.3"
  ├─ memory_usage: "68.1"
  └─ last_heartbeat: "2025-11-28T10:00:00.123456"

2️⃣ 매치메이킹 큐
matchmaking:queue                  (List)   FIFO 대기열
  └─ {"ticket_id":"abc","player_id":"123","timestamp":"..."}

ticket:{ticket_id}                 (Hash, TTL 5분)
  ├─ ticket_id: "abc123"
  ├─ player_id: "player-789"
  ├─ status: "MATCHED"  # QUEUED → MATCHED
  ├─ server_ip: "3.37.88.2"
  ├─ server_port: "7779"
  ├─ session_id: "game-server-3-37-88-2-7779"
  └─ updated_at: "2025-11-28T10:00:05"

3️⃣ 플레이어 세션 (재입장 지원)
player_session:{player_id}         (Hash, TTL 30분)
  ├─ player_id: "player-789"
  ├─ server_id: "game-server-3-37-88-2-7779"
  ├─ server_ip: "3.37.88.2"
  ├─ server_port: "7779"
  ├─ session_id: "game-server-3-37-88-2-7779"
  ├─ character_type: "warrior"
  ├─ character_name: "Hero123"
  ├─ status: "CONNECTED"
  ├─ joined_at: "2025-11-28T10:00:05"
  └─ last_seen: "2025-11-28T10:00:30"

player_data:{player_id}:{session_id} (Hash, TTL 30분)
  ├─ character_type: "warrior"
  ├─ character_name: "Hero123"
  └─ session_id: "game-server-3-37-88-2-7779"

4️⃣ 게임 세션 관리
session:{session_id}               (Hash)
  ├─ session_id: "game-server-3-37-88-2-7779"
  ├─ server_id: "game-server-3-37-88-2-7779"
  ├─ current_players: "45"
  ├─ max_players: "100"
  ├─ status: "IN_GAME"
  └─ created_at: "2025-11-28T09:50:00"

session:{session_id}:players       (Set)
  └─ "player-789"
  └─ "player-456"

sessions:active                    (Set)
  └─ "game-server-3-37-88-2-7779"
```

---

### Q5-2. TTL(Time To Live)을 왜 사용하나요?

**A:**

**TTL이 설정된 키:**
- `ticket:{ticket_id}`: 5분
- `player_session:{player_id}`: 30분
- `player_data:{player_id}:{session_id}`: 30분

**이유:**

**1) 메모리 최적화**
- 오래된 데이터 자동 삭제
- Redis 메모리 부족 방지

**2) 데이터 정합성**
- 만료된 티켓은 자동 삭제
- 30분 이상 접속 없는 플레이어 세션 정리

**3) 재입장 지원**
- 30분 이내 재접속 → 이전 캐릭터 정보 복원
- 30분 초과 → 새 게임 시작

**TTL 설정 코드:**
```python
# redis_client.py

def set_ticket_status(self, ticket_id, ...):
    self.client.hset(f"ticket:{ticket_id}", mapping=data)
    self.client.expire(f"ticket:{ticket_id}", 300)  # 5분

def register_player_session(self, player_id, ...):
    self.client.hset(f"player_session:{player_id}", mapping=data)
    self.client.expire(f"player_session:{player_id}", 1800)  # 30분
```

---

### Q5-3. 재입장 시스템은 어떻게 동작하나요?

**A:**

**흐름:**

**1) 플레이어 입장 시:**
```python
# Unity → POST /api/player-joined
{
  "player_id": "player-789",
  "server_id": "game-server-3-37-88-2-7779",
  "character_type": "warrior",
  "character_name": "Hero123"
}

# FastAPI → Redis 저장
redis.hset("player_session:player-789", {
    "server_id": "game-server-3-37-88-2-7779",
    "server_ip": "3.37.88.2",
    "server_port": "7779",
    "character_type": "warrior",
    "character_name": "Hero123",
    "status": "CONNECTED"
})
redis.expire("player_session:player-789", 1800)  # 30분
```

**2) 플레이어 연결 끊김 (브라우저 닫음):**
- Redis 데이터는 유지 (30분 TTL)
- 게임 서버는 플레이어를 "DISCONNECTED" 상태로 유지

**3) 플레이어 재접속 (30분 이내):**
```python
# Unity → POST /api/find-game
{
  "player_id": "player-789"
}

# FastAPI → 활성 세션 조회
active_session = redis_client.get_active_player_session("player-789")

if active_session:
    server_id = active_session["server_id"]
    server = redis_client.get_server_info(server_id)

    # 게임이 아직 진행 중이면
    if server["status"] == "IN_GAME":
        # 즉시 재입장 응답
        return MatchmakingResponse(
            success=True,
            status=MatchmakingStatus.MATCHED,
            server_ip=active_session["server_ip"],
            server_port=active_session["server_port"],
            session_id=active_session["session_id"],
            is_rejoin=True,  # 재입장 플래그
            message="Rejoining previous game"
        )

# Unity → GET /api/player-data?player_id=player-789&session_id=xyz
# 캐릭터 정보 조회
→ {
    "character_type": "warrior",
    "character_name": "Hero123"
}

# Unity → 게임 서버 재접속 (캐릭터 복원)
```

**4) 30분 초과:**
- Redis TTL 만료 → 세션 데이터 삭제
- 새 게임으로 매칭

---

## 6. 네트워크 & 보안

### Q6-1. CORS 문제는 어떻게 해결했나요?

**A:**

**FastAPI CORS 설정:**
```python
# main.py

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 오리진 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Nginx 설정:**
```nginx
# nginx_default.conf

location /api/ {
    proxy_pass http://127.0.0.1:8000/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # CORS 헤더 (FastAPI에서 처리하므로 불필요)
}
```

**주의:**
- 프로덕션에서는 `allow_origins=["*"]` 대신 특정 도메인만 허용
- 예: `allow_origins=["https://stackguys.com"]`

---

### Q6-2. TLS/SSL은 어디에 적용되어 있나요?

**A:**

**1) ElastiCache Redis (TLS 활성화)**
```python
redis.Redis(
    host='master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com',
    port=6379,
    ssl=True,  # TLS 암호화
    ssl_cert_reqs=None,
    ssl_check_hostname=False
)
```

**2) Unity 게임 서버 (WSS)**
- 자체 서명 인증서 사용
- 포트: 8779 (WSS), 7779 (WS)

**3) ALB (현재 HTTP)**
- 프로덕션에서는 HTTPS 적용 필요
- ACM (AWS Certificate Manager)으로 무료 인증서 발급

**4) Nginx (현재 HTTP)**
- Let's Encrypt로 HTTPS 적용 가능

---

### Q6-3. DDoS 공격 방어는?

**A:**

**현재 적용된 방어:**

**1) AWS Shield Standard (기본 제공)**
- Layer 3/4 DDoS 공격 자동 방어
- SYN/UDP Flood 방어

**2) ALB Rate Limiting (미적용)**
- AWS WAF로 추가 가능
- IP당 초당 요청 수 제한

**3) Security Group**
- 필요한 포트만 개방
- 출발지 IP 제한

**추가 가능한 방어:**

**1) AWS WAF (Web Application Firewall)**
- SQL Injection, XSS 차단
- Rate Limiting 규칙
- GeoIP 차단

**2) CloudFront CDN**
- 엣지 로케이션에서 트래픽 분산
- DDoS 공격 흡수

**3) FastAPI Rate Limiting**
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/find-game")
@limiter.limit("10/minute")  # 분당 10회 제한
async def find_game(...):
    ...
```

---

## 7. 성능 & 확장성

### Q7-1. 순차 채우기 전략의 장단점은?

**A:**

**순차 채우기 (Fill Sequential):**
- 가장 많이 찬 서버부터 우선 배정
- current_players 같으면 작은 포트 우선

**장점:**

**1) 비용 절감**
- 빈 서버 최소화
- ASG 축소 시 빈 서버부터 종료 가능

**2) 플레이어 밀집**
- 서버당 플레이어 수가 많음
- 활기찬 게임 환경

**3) 서버 효율성**
- 서버 리소스 집중 사용
- 유휴 서버 최소화

**예시:**
```
초기 상태:
Server A: 0/100
Server B: 0/100
Server C: 0/100

100명 매칭 시:
Server A: 100/100  ✅ 가득 참
Server B: 0/100
Server C: 0/100

→ Server B, C는 종료 가능 (비용 절감)
```

**단점:**

**1) 핫스팟 발생**
- 한 서버에 부하 집중
- CPU/메모리 사용률 높음

**2) 신규 서버 활용 지연**
- ASG로 새 서버 생성해도 기존 서버 우선 사용

**대안: 라운드 로빈 (Round Robin)**
- 서버를 골고루 사용
- 부하 분산
- 단점: 빈 서버 많음

---

### Q7-2. 매치메이킹 서버가 여러 개일 때 어떤 이점이 있나요?

**A:**

**장점:**

**1) API 요청 처리량 증가**
- ALB가 여러 서버로 트래픽 분산
- 동시 처리 가능한 요청 수 증가

**2) 하트비트 처리 안정화**
- 게임 서버 100개 → 하트비트 3000req/min
- 매치메이킹 서버 1개 → 부하 집중
- 매치메이킹 서버 10개 → 300req/min/서버

**3) 장애 복구**
- 한 서버 장애 시 ALB가 자동으로 제외
- 나머지 서버로 트래픽 분산
- 무중단 서비스

**4) 대기열 처리 병렬화**
- 각 서버의 백그라운드 워커가 독립적으로 대기열 처리
- 큐에서 플레이어를 동시에 꺼내서 매칭
- 간접적인 매칭 속도 향상

**주의:**
- Lua Script 덕분에 Race Condition 없음
- 여러 서버가 동시에 같은 게임 서버에 배정해도 안전

---

### Q7-3. Brotli 압축의 효과는?

**A:**

**Nginx Brotli 설정:**
```nginx
# nginx_default.conf

brotli on;
brotli_comp_level 6;
brotli_static on;
brotli_types text/plain text/css application/javascript application/json application/wasm;

location ~* \.wasm$ {
    default_type application/wasm;
}
```

**효과:**

**1) 파일 크기 감소**
- Unity WebGL 빌드:
  - Client.wasm: ~50MB → ~15MB (70% 감소)
  - Client.framework.js: ~10MB → ~3MB (70% 감소)
  - Client.data: ~30MB → ~10MB (67% 감소)

**2) 로딩 시간 단축**
- 4G 환경 (5Mbps): 80초 → 25초
- WiFi 환경 (50Mbps): 8초 → 2.5초

**3) 대역폭 비용 절감**
- CloudFront 사용 시 데이터 전송 비용 70% 감소

**주의:**
- CPU 사용량 약간 증가 (압축 처리)
- `brotli_comp_level 6`이 성능/압축률 균형점

---

### Q7-4. 게임 서버 ASG 스케일링 정책은?

**A:**

**현재 설정 (CPU 기반):**
```
스케일링 정책: Target Tracking
메트릭: Average CPU Utilization
목표값: 70%
인스턴스 Warm-up: 300초 (5분)
```

**동작 방식:**
1. CloudWatch가 ASG 평균 CPU 수집
2. 70% 초과 → Scale Out
3. 70% 미달 → Scale In

**문제점:**
- CPU가 낮아도 대기열이 많으면 확장 필요
- 게임 특성상 CPU보다 플레이어 수가 중요

**개선안: 커스텀 메트릭 (큐 길이 기반)**
```python
# main.py

import boto3

cloudwatch = boto3.client('cloudwatch', region_name='ap-northeast-2')

def publish_queue_metric():
    """매칭 대기열 길이를 CloudWatch에 전송"""
    queue_length = redis_client.get_queue_length()

    cloudwatch.put_metric_data(
        Namespace='StackGuys/Matchmaking',
        MetricData=[
            {
                'MetricName': 'QueueLength',
                'Value': queue_length,
                'Unit': 'Count'
            }
        ]
    )

# 1분마다 전송
scheduler.add_job(publish_queue_metric, 'interval', minutes=1)
```

**스케일링 정책 (CloudWatch):**
```
메트릭: Custom - QueueLength
목표값: 50 (대기열 50명 이하 유지)
스케일 아웃: 큐 길이 > 50 → 게임 서버 추가
스케일 인: 큐 길이 < 10 → 게임 서버 제거
```

---

## 8. 테스트 & 모니터링

### Q8-1. test_concurrent.py는 무엇을 테스트하나요?

**A:**

**목적:**
- 동시 매칭 요청 시 Race Condition 발생 여부 검증
- 순차 채우기 전략 정상 동작 확인

**사용법:**
```bash
cd matchmaking
python3 test_concurrent.py 100  # 100명 동시 요청
```

**검증 항목:**

**1) Race Condition 방지**
```python
# 100명 동시 요청 → 서버 정원 초과 없이 정확히 분배
Server A: 100/100 ✅
Server B: 0/100 ✅
```

**2) 순차 채우기**
```python
# Server A가 100명 차기 전에 Server B로 배정되면 ❌
if server_7780 > 0 and server_7779 < 100:
    print("⚠️ 경고: 7779가 {server_7779}명인데 7780에 {server_7780}명 배정됨!")
else:
    print("✅ 순차 채우기 정상 동작")
```

**3) 매칭 성공률**
```python
✅ 매칭 성공: 100명
⏰ 타임아웃: 0명
❌ 에러: 0명
```

**4) 응답 시간**
```python
⏱️ 총 소요 시간: 3.42초
→ 평균: 34ms/요청
```

---

### Q8-2. ConsoleBot은 무엇을 테스트하나요?

**A:**

**목적:**
- Unity 게임 서버에 실제 WebSocket 연결
- AI 봇이 플레이어처럼 행동하며 부하 테스트

**주요 기능:**

**1) 2가지 모드**
```bash
# 매치메이킹 모드 (자동 서버 할당)
> match
> start 200

# 수동 접속 모드 (특정 서버 테스트)
> server 3.37.88.2 7779
> start 50
```

**2) AI 봇 행동**
```csharp
// 30 FPS로 입력 전송
while (true) {
    SendJson("MovePlayer", $"{moveX},{moveY}");

    // 5-7초마다 방향 전환
    if (rotateTimer < 0) {
        moveX = Random(-1.0, 1.0);
        moveY = Random(-1.0, 1.0);
    }

    // 3-5초마다 점프
    if (jumpTimer < 0) {
        SendJson("JumpPlayer", "");
    }

    await Task.Delay(33);  // 30 FPS
}
```

**3) 성능 최적화**
```csharp
// ThreadPool 설정
ThreadPool.SetMinThreads(1000, 1000);

// HTTP 연결 풀
var handler = new SocketsHttpHandler {
    MaxConnectionsPerServer = int.MaxValue,
    PooledConnectionLifetime = TimeSpan.FromMinutes(10)
};
```

**검증 항목:**

**1) 서버 동시 접속 처리**
- 게임 서버가 100명 동시 접속 처리 가능?
- CPU/메모리 사용량은?

**2) 매치메이킹 처리량**
- 200명 동시 매칭 요청 → 응답 시간?
- 타임아웃 발생 비율?

**3) WebSocket 안정성**
- 장시간 연결 유지?
- 패킷 손실 여부?

**4) 재연결 로직**
- 연결 끊김 시 자동 재시도?
- 최대 5회 시도 후 종료?

---

### Q8-3. CloudWatch 모니터링은?

**A:**

**현재 수집 중인 메트릭:**

**1) EC2 기본 메트릭 (CloudWatch Agent 없이)**
- CPUUtilization
- NetworkIn/NetworkOut
- DiskReadBytes/DiskWriteBytes

**2) ALB 메트릭**
- RequestCount
- TargetResponseTime
- HealthyHostCount/UnhealthyHostCount
- HTTPCode_Target_2XX_Count
- HTTPCode_Target_5XX_Count

**3) ElastiCache 메트릭**
- CPUUtilization
- DatabaseMemoryUsagePercentage
- CurrConnections (현재 연결 수)
- Evictions (메모리 부족으로 삭제된 키)
- CacheMisses/CacheHits

**추가 가능한 메트릭:**

**1) 커스텀 메트릭 (boto3)**
```python
import boto3

cloudwatch = boto3.client('cloudwatch')

# 대기열 길이
cloudwatch.put_metric_data(
    Namespace='StackGuys/Matchmaking',
    MetricData=[
        {
            'MetricName': 'QueueLength',
            'Value': redis_client.get_queue_length(),
            'Unit': 'Count'
        }
    ]
)

# 활성 게임 서버 수
cloudwatch.put_metric_data(
    Namespace='StackGuys/GameServer',
    MetricData=[
        {
            'MetricName': 'ActiveServers',
            'Value': len(redis_client.get_available_servers()),
            'Unit': 'Count'
        }
    ]
)
```

**2) 알람 설정**
```
메트릭: QueueLength
조건: > 100 (대기열 100명 초과)
알람: SNS → Email/SMS 알림
액션: ASG Scale Out
```

---

### Q8-4. 로그는 어떻게 확인하나요?

**A:**

**1) 매치메이킹 서버 로그**
```bash
# systemd 서비스 로그
sudo journalctl -u matchmaking -f

# 최근 100줄
sudo journalctl -u matchmaking -n 100

# 특정 시간대
sudo journalctl -u matchmaking --since "2025-11-28 10:00" --until "2025-11-28 11:00"
```

**2) 게임 서버 로그**
```bash
# 7779 포트 게임 서버
sudo journalctl -u game-server@7779 -f

# 모든 게임 서버
sudo journalctl -u 'game-server@*' -f
```

**3) Nginx 로그**
```bash
# Access Log (요청 기록)
sudo tail -f /var/log/nginx/access.log

# Error Log (에러만)
sudo tail -f /var/log/nginx/error.log
```

**4) FastAPI 앱 로그 (uvicorn)**
```bash
# 직접 실행 시
uvicorn main:app --log-level info

# systemd 서비스
sudo journalctl -u matchmaking -f
```

**5) Redis 로그 (ElastiCache)**
- AWS Console → ElastiCache → Logs
- CloudWatch Logs로 전송 설정 가능

---

## 9. 트러블슈팅

### Q9-1. 매칭이 타임아웃되는 이유는?

**A:**

**가능한 원인:**

**1) 사용 가능한 게임 서버 없음**
```bash
# Redis 확인
redis-cli -h master.matchmaking-redis... --tls
> SMEMBERS servers:all
> HGETALL server:game-server-3-37-88-2-7779
```

**체크 사항:**
- `servers:all`에 서버 ID 있는지?
- `status`가 "AVAILABLE" 또는 "STARTING"인지?
- `current_players` < `max_players`인지?
- `last_heartbeat`가 60초 이내인지?

**해결:**
```bash
# 게임 서버 실행 확인
sudo systemctl status game-server@7779

# 게임 서버 재시작
sudo systemctl restart game-server@7779
```

**2) 백그라운드 워커 미실행**
```bash
# 매치메이킹 서버 로그 확인
sudo journalctl -u matchmaking -f | grep "process_matchmaking"

# 워커가 실행되지 않으면
sudo systemctl restart matchmaking
```

**3) Redis 연결 끊김**
```bash
# Redis 연결 확인
curl http://matchmaking-alb.../health
# "redis": "disconnected" → 문제

# 보안 그룹 확인
# matchmaking-server-sg → elasticache-redis-sg (6379 포트)
```

---

### Q9-2. 게임 서버가 등록되지 않는 이유는?

**A:**

**가능한 원인:**

**1) EC2 Public IP 감지 실패**
```csharp
// Unity 로그 확인
sudo journalctl -u game-server@7779 -f | grep "EC2"

// 정상:
✅ [EC2] Auto-detected Public IP: 3.37.88.2

// 실패:
❌ [EC2] Failed to get Public IP, using 127.0.0.1
```

**해결:**
```csharp
// NetworkGameManager.cs
string GetEC2PublicIP()
{
    // EC2 메타데이터 API 타임아웃 증가
    request.timeout = 10;  // 5 → 10초
}
```

**2) 매치메이킹 서버 URL 오류**
```csharp
// Unity 로그
❌ POST http://3.34.45.60:8000/api/server/register 404

// 해결: ALB 도메인 사용
const string MatchmakingURL = "http://matchmaking-alb-1609632759.../api/server/register";
```

**3) CORS 에러**
```bash
# 브라우저 콘솔
Access to fetch at 'http://...' from origin 'http://...' has been blocked by CORS policy

# FastAPI CORS 확인
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

---

### Q9-3. Redis 메모리 부족 문제는?

**A:**

**증상:**
```bash
redis-cli> INFO memory
used_memory_human: 12.5G
maxmemory_human: 13.07G  # 거의 다 참!

# Evictions 발생
evicted_keys: 1543  # 키가 자동 삭제됨
```

**원인:**
- TTL 없는 키 누적
- 대용량 데이터 저장

**해결:**

**1) TTL 설정 확인**
```python
# redis_client.py

# 잘못된 예:
self.client.hset(f"server:{server_id}", mapping=data)
# TTL 없음 → 영구 저장

# 올바른 예:
self.client.hset(f"ticket:{ticket_id}", mapping=data)
self.client.expire(f"ticket:{ticket_id}", 300)  # 5분 TTL
```

**2) 수동 정리**
```bash
redis-cli --tls -h master.matchmaking-redis...

# 오래된 티켓 삭제
> SCAN 0 MATCH ticket:* COUNT 100
> DEL ticket:old-ticket-id

# 만료된 플레이어 세션 삭제
> SCAN 0 MATCH player_session:* COUNT 100
```

**3) Redis 노드 타입 업그레이드**
```
cache.r7g.large (13GB)
  ↓
cache.r7g.xlarge (26GB)
```

**4) Eviction Policy 설정**
```
AWS Console → ElastiCache → Parameter Groups
maxmemory-policy: allkeys-lru  # LRU로 오래된 키 자동 삭제
```

---

### Q9-4. ASG가 확장되지 않는 이유는?

**A:**

**게임 서버 ASG:**

**1) CloudWatch 알람 확인**
```bash
# AWS Console → CloudWatch → Alarms
# TargetTracking-stack-guys-game-server-asg-AlarmHigh-xxx

# 상태: In alarm → 확장되어야 함
# 상태: OK → CPU 70% 미만 (확장 안함)
```

**2) CPU 사용률 확인**
```bash
# 게임 서버 SSH 접속
ssh -i key.pem ubuntu@3.37.88.2

# CPU 사용률
top

# Unity 프로세스 확인
ps aux | grep Build_Server.x86_64
```

**3) Warm-up Time**
```
# ASG 설정
Instance Warmup: 300초 (5분)

# 확장 후 5분간은 메트릭에 포함 안됨
# 인내심 필요!
```

**4) 최대 용량 도달**
```
현재 인스턴스: 5개
최대 용량: 5개
→ 더 이상 확장 불가
```

**매치메이킹 서버 ASG (미구축):**
- 아직 ASG 생성 안됨
- AMI 생성 필요
- Launch Template 생성 필요

---

### Q9-5. WebSocket 연결이 실패하는 이유는?

**A:**

**증상:**
```javascript
WebSocket connection to 'wss://3.37.88.2:8779/' failed:
```

**원인:**

**1) SSL 인증서 문제 (WSS)**
```bash
# 자체 서명 인증서 → 브라우저 경고
NET::ERR_CERT_AUTHORITY_INVALID
```

**해결:**
- 브라우저에서 고급 → 계속 진행 클릭
- 또는 WS (비암호화) 사용: `ws://3.37.88.2:7779/`

**2) 포트 막힘**
```bash
# Security Group 확인
game-server-sg:
  Inbound: 7779-7790, 8779-8790 (TCP) ← 0.0.0.0/0
```

**3) 게임 서버 미실행**
```bash
# 게임 서버 상태 확인
sudo systemctl status game-server@7779

# 프로세스 확인
ps aux | grep "Build_Server.x86_64.*-port 7779"
```

**4) Unity 서버 WebSocket 모드 비활성화**
```csharp
// NetworkGameManager.cs
var transport = NetworkManager.Singleton.GetComponent<UnityTransport>();
transport.UseWebSockets = true;  // ✅ 활성화 필요
```

---

## 📚 추가 참고 자료

### 관련 파일 위치

- **매치메이킹 서버**: `D:\JUNGLE\web\stack-guys-web\matchmaking\`
- **WebGL 클라이언트**: `D:\JUNGLE\Build\misc\final_index.html`
- **Nginx 설정**: `D:\JUNGLE\web\stack-guys-web\nginx_default.conf`
- **테스트 도구**: `D:\JUNGLE\web\stack-guys-web\ConsoleBot\`
- **README**: `D:\JUNGLE\web\stack-guys-web\README.md`

### AWS 리소스

- **WebGL ALB**: stackguys-alb
- **매치메이킹 ALB**: matchmaking-alb-1609632759.ap-northeast-2.elb.amazonaws.com
- **ElastiCache**: master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com
- **게임 서버 ASG**: stack-guys-game-server-asg2
- **키 페어**: unity-webgl-website-key-seoul

---

**작성일**: 2025-11-28
**버전**: 1.0
**문서 위치**: `D:\JUNGLE\web\stack-guys-web\POST_SESSION_QNA.md`
