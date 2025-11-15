# Stack Guys Matchmaking Server

FastAPI + Redis 기반 매치메이킹 서버

## 📋 기능

1. **매치메이킹 큐 관리**
   - 플레이어 매칭 요청 큐잉
   - 티켓 기반 상태 추적

2. **게임 서버 관리**
   - 서버 등록 및 상태 추적
   - 하트비트 모니터링
   - 서버 부하 관리

3. **세션 관리**
   - 게임 세션 생성/종료
   - 플레이어-세션 매핑

## 🚀 빠른 시작

### 1. 의존성 설치

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Redis 설치 및 실행

```bash
# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis-server

# 또는 Docker
docker run -d -p 6379:6379 redis:latest
```

### 3. 환경 변수 설정

`.env` 파일을 수정하여 설정 변경

```env
REDIS_HOST=localhost
REDIS_PORT=6379
GAME_SERVER_IP=172.31.34.164
```

### 4. 서버 실행

```bash
# 개발 모드
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 또는 스크립트 사용
chmod +x run.sh
./run.sh
```

## 📡 API 엔드포인트

### Health Check
- `GET /` - 기본 헬스 체크
- `GET /health` - 상세 헬스 체크

### Matchmaking
- `POST /api/find-game` - 매칭 요청
- `GET /api/ticket-status?ticket_id={id}&player_id={id}` - 티켓 상태 조회

### Game Server Management
- `POST /api/server/register` - 서버 등록
- `POST /api/server/heartbeat` - 서버 하트비트
- `GET /api/servers` - 전체 서버 목록
- `GET /api/servers/available` - 사용 가능한 서버 목록

## 📊 Redis 데이터 구조

```
matchmaking:queue           - List: 매칭 대기 큐
ticket:{ticket_id}          - Hash: 티켓 정보
server:{server_id}          - Hash: 서버 정보
servers:all                 - Set: 전체 서버 목록
session:{session_id}        - Hash: 세션 정보
session:{session_id}:players - Set: 세션 플레이어
sessions:active             - Set: 활성 세션 목록
```

## 🔧 설정

### 매치메이킹 설정
- `MAX_PLAYERS_PER_SESSION`: 세션당 최대 플레이어 수 (기본: 8)
- `MATCHMAKING_TIMEOUT`: 매칭 타임아웃 (기본: 60초)

### 게임 서버 설정
- `GAME_SERVER_PORT_START`: 포트 범위 시작 (기본: 7779)
- `GAME_SERVER_PORT_END`: 포트 범위 끝 (기본: 7790)

## 📈 운영

### 로그 확인
```bash
# 실시간 로그
tail -f /var/log/matchmaking.log

# uvicorn 로그
uvicorn main:app --log-level info
```

### Redis 모니터링
```bash
redis-cli
> INFO
> KEYS *
> LLEN matchmaking:queue
```

### 프로덕션 배포

```bash
# systemd 서비스 파일 생성
sudo nano /etc/systemd/system/matchmaking.service

# 서비스 활성화
sudo systemctl enable matchmaking
sudo systemctl start matchmaking
```

## 🏗️ 아키텍처

```
WebGL Client → Matchmaking Server → Game Server
                    ↓
                  Redis
```

## 📝 TODO

- [ ] ASG 연동
- [ ] 멀티 게임 서버 지원
- [ ] 매칭 알고리즘 개선
- [ ] 로깅 및 모니터링
- [ ] 보안 강화 (API 키, Rate limiting)
