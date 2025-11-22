#!/usr/bin/env python3
"""
동시 매칭 요청 테스트 스크립트

사용법:
    python3 test_concurrent.py 100  # 100명 동시 요청
    python3 test_concurrent.py 500  # 500명 동시 요청
"""

import asyncio
import aiohttp
import sys
import time
from collections import Counter

MATCHMAKING_URL = "http://localhost:8000"


async def request_matchmaking(session, player_num):
    """단일 매칭 요청"""
    player_id = f"test-player-{player_num}"

    try:
        # /api/find-game 요청
        async with session.post(
            f"{MATCHMAKING_URL}/api/find-game",
            json={"player_id": player_id}
        ) as resp:
            data = await resp.json()
            ticket_id = data.get("ticket_id")

            # 티켓 상태 폴링 (최대 10초)
            for _ in range(20):
                await asyncio.sleep(0.5)

                async with session.get(
                    f"{MATCHMAKING_URL}/api/ticket-status",
                    params={"ticket_id": ticket_id, "player_id": player_id}
                ) as status_resp:
                    status_data = await status_resp.json()

                    if status_data["status"] == "MATCHED":
                        return {
                            "player_id": player_id,
                            "server_ip": status_data["server_ip"],
                            "server_port": status_data["server_port"],
                            "session_id": status_data["session_id"]
                        }

            return {"player_id": player_id, "status": "TIMEOUT"}

    except Exception as e:
        return {"player_id": player_id, "error": str(e)}


async def test_concurrent_matchmaking(num_players):
    """동시 매칭 테스트"""
    print(f"\n🚀 {num_players}명 동시 매칭 테스트 시작...\n")

    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        # 동시에 모든 요청 전송
        tasks = [request_matchmaking(session, i) for i in range(1, num_players + 1)]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - start_time

    # 결과 분석
    server_distribution = Counter()
    matched = 0
    timeout = 0
    errors = 0

    for result in results:
        if "error" in result:
            errors += 1
        elif result.get("status") == "TIMEOUT":
            timeout += 1
        elif "server_port" in result:
            matched += 1
            server_key = f"{result['server_ip']}:{result['server_port']}"
            server_distribution[server_key] += 1

    # 결과 출력
    print(f"⏱️  총 소요 시간: {elapsed:.2f}초")
    print(f"✅ 매칭 성공: {matched}명")
    print(f"⏰ 타임아웃: {timeout}명")
    print(f"❌ 에러: {errors}명")
    print(f"\n📊 서버별 분배 현황:")

    for server, count in sorted(server_distribution.items(), key=lambda x: x[0]):
        percentage = (count / matched * 100) if matched > 0 else 0
        print(f"   {server}: {count}명 ({percentage:.1f}%)")

    print(f"\n🎯 순차 채우기 검증:")
    ports = sorted([int(s.split(':')[1]) for s in server_distribution.keys()])

    if len(ports) >= 2:
        # 7779가 100명 차기 전에 7780으로 간 사람이 있는지 체크
        server_7779 = server_distribution.get(f"3.37.88.2:7779", 0)
        server_7780 = server_distribution.get(f"3.37.88.2:7780", 0)

        if server_7780 > 0 and server_7779 < 100:
            print(f"   ⚠️  경고: 7779가 {server_7779}명인데 7780에 {server_7780}명 배정됨!")
        else:
            print(f"   ✅ 순차 채우기 정상 동작")
    else:
        print(f"   ℹ️  단일 서버만 사용됨")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 test_concurrent.py <동시_플레이어_수>")
        print("예시: python3 test_concurrent.py 100")
        sys.exit(1)

    num_players = int(sys.argv[1])
    asyncio.run(test_concurrent_matchmaking(num_players))
