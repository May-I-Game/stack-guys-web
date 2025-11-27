# Stack Guys Infra Server

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
├── nginx_default.conf       # Nginx 설정 (WebGL 서빙)
└── final_index.html         # Unity WebGL 클라이언트 HTML
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
- **백그라운드 스케줄러**: 죽은 서버 자동 정리

## AWS 인프라

- **ALB**: stackguys-alb
- **Target Group**: Health check `/health`
- **ElastiCache Redis**: master.matchmaking-redis.ee8ufb.apn2.cache.amazonaws.com
- **EC2 Auto Scaling Group**: 웹 서버 및 게임 서버 자동 스케일링
- **Security Group**: 포트 80 (HTTP), 8000 (FastAPI), 7779-7790 (게임 서버)

## 테스트

### 동시 매칭 요청 테스트 (Race Condition 검증)

**파일**: `matchmaking/test_concurrent.py`

동시에 수백 명의 플레이어가 매칭 요청을 보내는 상황을 시뮬레이션하여 **Lua Script 원자적 할당**이 제대로 작동하는지 검증하는 테스트 도구입니다.

**기능**:
- N명의 플레이어 동시 매칭 요청 시뮬레이션
- 서버별 플레이어 분배 현황 분석
- 순차 채우기 전략 검증 (7779가 100명 차기 전에 7780으로 배정되는지 체크)
- Race Condition 발생 여부 확인

**사용법**:
```bash
cd matchmaking

# 100명 동시 요청 테스트
python3 test_concurrent.py 100

# 500명 동시 요청 테스트
python3 test_concurrent.py 500
```

**출력 예시**:
```
🚀 100명 동시 매칭 테스트 시작...

⏱️  총 소요 시간: 3.42초
✅ 매칭 성공: 100명
⏰ 타임아웃: 0명
❌ 에러: 0명

📊 서버별 분배 현황:
   3.37.88.2:7779: 100명 (100.0%)

🎯 순차 채우기 검증:
   ✅ 순차 채우기 정상 동작
```

**검증 항목**:
1. **Race Condition 방지**: 동시 요청 시 서버 정원 초과 없이 정확히 분배
2. **순차 채우기**: 낮은 포트 서버부터 순차적으로 채움 (7779 → 7780 → 7781)
3. **매칭 성공률**: 타임아웃 및 에러 없이 모든 플레이어 매칭
4. **응답 시간**: 대규모 동시 요청 처리 성능 측정

## 참고

- 매치메이킹 서버 상세: `matchmaking/README.md`
- Unity WebGL 클라이언트: `final_index.html`
- Nginx 설정: `nginx_default.conf`
<<<<<<< Updated upstream

=======
- 동시성 테스트: `matchmaking/test_concurrent.py`
>>>>>>> Stashed changes
