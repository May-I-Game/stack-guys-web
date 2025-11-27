# Stack Guys Web Server

Unity WebGL 멀티플레이어 게임을 위한 웹 서버 및 매치메이킹 인프라

## 프로젝트 구조

```
stack-guys-web/
├── matchmaking/              # 매치메이킹 서버 (FastAPI + Redis)
│   ├── main.py              # FastAPI 앱 (매치메이킹 로직, 서버 관리)
│   ├── redis_client.py      # Redis 클라이언트 (Lua Script 기반 원자적 서버 할당)
│   ├── models.py            # Pydantic 데이터 모델
│   ├── config.py            # 환경 설정 (Redis, 포트 등)
│   ├── requirements.txt     # Python 의존성
│   ├── .env                 # 환경 변수
│   └── run.sh              # 서버 실행 스크립트
├── nginx_default.conf       # Nginx 설정 (WebGL 서빙 + API 프록시)
└── final_index.html         # Unity WebGL 클라이언트 HTML
```

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                     WebGL Client (Browser)                   │
│              (Unity Game + Matchmaking UI)                   │
└─────────────────┬───────────────────────────────────────────┘
                  │ HTTP
                  ↓
┌─────────────────────────────────────────────────────────────┐
│                   AWS Application Load Balancer              │
│                  (Health Check: /health)                     │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ↓
┌─────────────────────────────────────────────────────────────┐
│               EC2 Instance (Web Server)                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Nginx (Port 80)                                      │  │
│  │  - Unity WebGL 정적 파일 서빙                         │  │
│  │  - Brotli 압축                                        │  │
│  │  - /api/* → FastAPI 프록시                            │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │  FastAPI (Port 8000)                                  │  │
│  │  - 매치메이킹 API                                      │  │
│  │  - 게임 서버 관리 API                                  │  │
│  │  - 플레이어 세션 관리                                  │  │
│  └───────────────────────┬───────────────────────────────┘  │
└────────────────────────┬─┴───────────────────────────────────┘
                         │
                         ↓
        ┌────────────────────────────────┐
        │   AWS ElastiCache Redis        │
        │   (TLS/SSL 활성화)             │
        │   - 매치메이킹 큐              │
        │   - 게임 서버 상태             │
        │   - 플레이어 세션              │
        └────────────────────────────────┘
                         ↑
                         │ Heartbeat (30초마다)
                         │
        ┌────────────────┴───────────────┐
        │  EC2 Instance (Game Server)    │
        │  - Unity Dedicated Server      │
        │  - 포트: 7779-7790             │
        │  - Auto Scaling Group          │
        └────────────────────────────────┘
```

## 주요 기능

### 1. Unity WebGL 클라이언트 서빙
- **파일**: `final_index.html`
- Nginx를 통한 정적 파일 서빙
- iOS Chrome 모바일 최적화 (주소창 잘림 문제 해결)
- 세로모드 차단 (가로 전용)
- LocalStorage 기반 player_id 자동 생성

### 2. 매치메이킹 서버
- **FastAPI + Redis** 기반
- **Lua Script 원자적 서버 할당** (Race Condition 방지)
- 순차 채우기 전략 (서버를 순차적으로 채워 효율성 극대화)
- 재입장 지원 (플레이어 세션 30분 TTL)
- 자동 서버 정리 (5분마다 죽은 서버 삭제)

### 3. 게임 서버 관리
- Unity Dedicated Server 등록 및 하트비트 모니터링
- 게임 종료 후 자동 프로세스 종료 (SSH 기반)
- ASG 대응 (동적 IP 지원)
- 서버 상태 추적 (AVAILABLE, STARTING, IN_GAME)

### 4. 플레이어 세션 관리
- 게임 입장 시 캐릭터 정보 저장
- 재입장 시 이전 캐릭터 복원
- Redis TTL 기반 자동 세션 정리 (30분)

## API 엔드포인트

### Matchmaking
- `POST /api/find-game` - 매칭 요청
- `GET /api/ticket-status?ticket_id={id}&player_id={id}` - 티켓 상태 조회

### Game Server Management
- `POST /api/server/register` - 서버 등록
- `POST /api/server/heartbeat` - 하트비트
- `POST /api/server/game-ended` - 게임 종료 신호
- `POST /api/servers/kill/{server_id}` - 서버 강제 종료
- `GET /api/servers` - 전체 서버 목록
- `GET /api/servers/available` - 사용 가능한 서버 목록
- `POST /api/servers/cleanup` - 죽은 서버 정리

### Player Session
- `POST /api/player-joined` - 플레이어 입장 알림
- `GET /api/player-data?player_id={id}&session_id={id}` - 플레이어 데이터 조회

### Health Check
- `GET /health` - 헬스 체크

## 배포 및 실행

### 1. 의존성 설치
```bash
cd matchmaking
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 환경 변수 설정
`.env` 파일에서 Redis 정보 설정

### 3. Nginx 설정
```bash
sudo cp nginx_default.conf /etc/nginx/sites-available/default
sudo systemctl restart nginx
```

### 4. 매치메이킹 서버 실행
```bash
cd matchmaking
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. systemd 서비스 등록 (자동 시작/재시작)
```bash
sudo systemctl enable matchmaking
sudo systemctl start matchmaking
```

## Redis 데이터 구조

```
matchmaking:queue                    - List: 매칭 대기 큐
ticket:{ticket_id}                   - Hash: 티켓 정보 (5분 TTL)
server:{server_id}                   - Hash: 서버 정보
servers:all                          - Set: 전체 서버 ID 목록
player_session:{player_id}           - Hash: 플레이어 세션 (30분 TTL)
player_data:{player_id}:{session_id} - Hash: 캐릭터 데이터 (30분 TTL)
session:{session_id}:players         - Set: 세션 플레이어 목록
sessions:active                      - Set: 활성 세션 목록
```

## 핵심 기술

- **FastAPI**: 고성능 비동기 Python 웹 프레임워크
- **Redis**: 인메모리 데이터베이스 (매칭 큐, 서버 상태 관리)
- **Lua Script**: Redis 원자적 연산 (Race Condition 방지)
- **Nginx**: 리버스 프록시 및 정적 파일 서빙
- **AWS ElastiCache**: 관리형 Redis (TLS/SSL)
- **AWS ALB**: 로드 밸런싱 및 Health Check
- **systemd**: 서비스 자동 시작/재시작 관리

## 모니터링

### 로그 확인
```bash
# 매치메이킹 서버 로그
sudo journalctl -u matchmaking -f

# Nginx 로그
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Redis 모니터링
```bash
redis-cli -h master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com --tls
> INFO
> KEYS *
> LLEN matchmaking:queue
```

## 성능 최적화

- **Lua Script 원자적 할당**: 동시 요청 시 Race Condition 방지
- **순차 채우기**: 서버를 순차적으로 채워 빈 서버 최소화
- **Brotli 압축**: WebGL 번들 크기 감소 (WASM, JS, Data)
- **Redis TTL**: 자동 데이터 정리로 메모리 최적화
- **백그라운드 스케줄러**: 죽은 서버 자동 정리 (5분마다)

## AWS 인프라

- **ALB**: stackguys-alb
- **Target Group**: Health check `/health`
- **ElastiCache Redis**: master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com
- **EC2 Auto Scaling Group**: 웹 서버 및 게임 서버 자동 스케일링
- **Security Group**: 포트 80 (HTTP), 8000 (FastAPI), 7779-7790 (게임 서버)

## 참고

- 매치메이킹 서버 상세: `matchmaking/README.md`
- Unity WebGL 클라이언트: `final_index.html`
- Nginx 설정: `nginx_default.conf`
