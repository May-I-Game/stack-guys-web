using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

class Program
{
    // === 설정 및 상태 변수 ===
    enum TestMode
    {
        Matchmaking, // 매치메이킹 API를 통해 접속
        Manual       // server 명령어로 지정한 IP로 직접 접속
    }

    static TestMode currentMode = TestMode.Matchmaking; // 기본값: 매치메이킹
    static Uri manualServerUri = null; // 수동 접속일 때 사용할 주소

    // 매치메이킹 서버 주소
    const string MatchMakerBaseUrl = "http://matchmaking-alb-1609632759.ap-northeast-2.elb.amazonaws.com";

    // HTTP 통신용 (매치메이킹용)
    static readonly HttpClient httpClient = new HttpClient(new SocketsHttpHandler
    {
        // 서버당 연결 수 제한 해제
        MaxConnectionsPerServer = int.MaxValue,

        // DNS 갱신을 위해 연결 수명 설정 (10분 정도가 적당)
        PooledConnectionLifetime = TimeSpan.FromMinutes(10)
    });

    // 봇 동작 설정
    const float rotateIntervalMin = 5.0f;
    const float rotateIntervalMax = 7.0f;
    const float jumpIntervalMin = 3.0f;
    const float jumpIntervalMax = 5.0f;

    // 봇 관리
    static ConcurrentDictionary<int, BotInstance> activeBots = new ConcurrentDictionary<int, BotInstance>();
    static int nextBotId = 0;
    static object lockObj = new object();

    class BotInstance
    {
        public int Id { get; set; }
        public ClientWebSocket WebSocket { get; set; }
        public CancellationTokenSource CancelToken { get; set; }
        public Task RunTask { get; set; }
        public TestMode Mode { get; set; } // 봇 생성 시점의 모드 저장
    }

    // JSON 모델
    class MatchInitResponse
    {
        public bool success { get; set; }
        public string ticket_id { get; set; }
        public string player_id { get; set; }
        public string status { get; set; }
    }

    class MatchStatusResponse
    {
        public string ticket_id { get; set; }
        public string status { get; set; }
        public string server_ip { get; set; }
        public int server_port { get; set; }
        public string session_id { get; set; }
    }

    static async Task Main(string[] args)
    {
        ThreadPool.SetMinThreads(1000, 1000);

        Console.WriteLine("=== 봇 부하 테스트 컨트롤러 ===");
        Console.WriteLine("명령어:");
        Console.WriteLine("  match               - [모드 변경] 매치메이킹 모드로 설정 (기본값)");
        Console.WriteLine("  server <ip> <port>  - [모드 변경] 수동 접속 모드로 설정");
        Console.WriteLine("  start <개수>        - 현재 모드로 봇 시작");
        Console.WriteLine("  stop                - 모든 봇 종료");
        Console.WriteLine("  status              - 현재 상태 및 모드 확인");
        Console.WriteLine("  exit                - 종료");
        Console.WriteLine("==========================================\n");

        _ = Task.Run(HandleCommands);
        await Task.Delay(-1);
    }

    static async Task HandleCommands()
    {
        while (true)
        {
            Console.Write("> ");
            string input = Console.ReadLine()?.Trim().ToLower();
            if (string.IsNullOrEmpty(input)) continue;

            string[] parts = input.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            string cmd = parts[0];

            try
            {
                switch (cmd)
                {
                    case "match":
                        currentMode = TestMode.Matchmaking;
                        Console.WriteLine($"모드 변경: [매치메이킹] (URL: {MatchMakerBaseUrl})");
                        break;

                    case "server":
                        if (parts.Length < 3)
                        {
                            Console.WriteLine("사용법: server <ip> <port>");
                            break;
                        }
                        SetManualServer(parts[1], parts[2]);
                        break;

                    case "start":
                        if (parts.Length < 2 || !int.TryParse(parts[1], out int startCount))
                        {
                            Console.WriteLine("사용법: start <개수>");
                            break;
                        }
                        await StartBots(startCount);
                        break;

                    case "stop":
                        await StopAllBots();
                        break;

                    case "status":
                        ShowStatus();
                        break;

                    case "exit":
                        await StopAllBots();
                        Environment.Exit(0);
                        break;

                    default:
                        Console.WriteLine("알 수 없는 명령어입니다.");
                        break;
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"오류: {ex.Message}");
            }
        }
    }

    static void SetManualServer(string ip, string portStr)
    {
        if (!int.TryParse(portStr, out int port))
        {
            Console.WriteLine("포트는 숫자여야 합니다.");
            return;
        }

        try
        {
            manualServerUri = new Uri($"ws://{ip}:{port}/");
            currentMode = TestMode.Manual; // 모드 자동 전환
            Console.WriteLine($"모드 변경: [수동 접속] 타겟: {manualServerUri}");
        }
        catch (Exception ex)
        {
            Console.WriteLine($"잘못된 주소 형식: {ex.Message}");
        }
    }

    static async Task StartBots(int count)
    {
        if (currentMode == TestMode.Manual && manualServerUri == null)
        {
            Console.WriteLine("오류: 수동 모드이지만 서버 주소가 설정되지 않았습니다. 'server IP Port'를 먼저 입력하세요.");
            return;
        }

        string modeStr = currentMode == TestMode.Matchmaking ? "매치메이킹" : $"수동({manualServerUri})";
        Console.WriteLine($"{count}개의 봇을 시작합니다... (모드: {modeStr})");

        for (int i = 0; i < count; i++)
        {
            int botId;
            lock (lockObj)
            {
                botId = nextBotId++;
            }

            var bot = new BotInstance
            {
                Id = botId,
                WebSocket = new ClientWebSocket(),
                CancelToken = new CancellationTokenSource(),
                Mode = currentMode // 봇 생성 시점의 모드를 기억
            };

            bot.RunTask = Task.Run(() => RunBotAI(bot));
            activeBots.TryAdd(botId, bot);

            // 서버 부하 분산
            await Task.Delay(currentMode == TestMode.Matchmaking ? 50 : 10);
        }

        Console.WriteLine($"{count}개 봇 시작 완료.");
    }

    static async Task StopAllBots()
    {
        Console.WriteLine("모든 봇 종료 중...");
        var allBots = activeBots.Values.ToList();
        activeBots.Clear();

        var closeTasks = allBots.Select(async bot =>
        {
            bot.CancelToken.Cancel();
            try
            {
                if (bot.WebSocket.State == WebSocketState.Open)
                {
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2)))
                        await bot.WebSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Stop", cts.Token);
                }
            }
            catch { }
            finally { bot.WebSocket.Dispose(); }
        });

        await Task.WhenAll(closeTasks);
        Console.WriteLine("종료 완료.");
    }

    static void ShowStatus()
    {
        Console.WriteLine("=== 현재 상태 ===");
        Console.WriteLine($"현재 모드: {currentMode}");
        if (currentMode == TestMode.Manual)
            Console.WriteLine($"타겟 서버: {manualServerUri}");
        else
            Console.WriteLine($"매치 서버: {MatchMakerBaseUrl}");

        int connected = activeBots.Values.Count(b => b.WebSocket.State == WebSocketState.Open);
        Console.WriteLine($"활성 봇: {activeBots.Count} (연결됨: {connected})");
        Console.WriteLine("================");
    }

    static async Task RunBotAI(BotInstance bot)
    {
        const int maxRetries = 5;
        int retryCount = 0;

        while (!bot.CancelToken.IsCancellationRequested && retryCount < maxRetries)
        {
            using (bot.WebSocket = new ClientWebSocket())
            {
                try
                {
                    Uri targetUri = null;

                    // 모드에 따른 주소 획득
                    if (bot.Mode == TestMode.Manual)
                    {
                        targetUri = manualServerUri;
                    }
                    else // Matchmaking
                    {
                        string myPlayerId = $"bot_{bot.Id}_{Guid.NewGuid().ToString().Substring(0, 8)}";
                        targetUri = await MatchMakeAsync(myPlayerId, bot.CancelToken.Token);
                    }

                    if (targetUri == null)
                    {
                        // 매칭 실패 혹은 설정 오류
                        retryCount++;
                        // 3초후 재시도
                        await Task.Delay(3000, bot.CancelToken.Token);
                        continue;
                    }

                    // 게임 서버 연결
                    await bot.WebSocket.ConnectAsync(targetUri, bot.CancelToken.Token);

                    // 행동 루프
                    Random rnd = new Random(Guid.NewGuid().GetHashCode());
                    float rotateTimer = 0, jumpTimer = 0, moveX = 0, moveY = 0;

                    // 초당 프레임 조절해서 부하 분산
                    const int targetFPS = 30;
                    const int TickMs = 1000 / targetFPS;
                    const float DeltaTime = 1.0f / targetFPS;

                    while (bot.WebSocket.State == WebSocketState.Open && !bot.CancelToken.Token.IsCancellationRequested)
                    {
                        await SendJson(bot.WebSocket, "MovePlayer", $"{moveX:F2},{moveY:F2}");

                        rotateTimer -= DeltaTime;
                        if (rotateTimer < 0)
                        {
                            moveX = (float)(rnd.NextDouble() * 2.0 - 1.0);
                            moveY = (float)(rnd.NextDouble() * 2.0 - 1.0);
                            rotateTimer = (float)(rnd.NextDouble() * (rotateIntervalMax - rotateIntervalMin) + rotateIntervalMin);
                        }

                        jumpTimer -= DeltaTime;
                        if (jumpTimer < 0)
                        {
                            await SendJson(bot.WebSocket, "JumpPlayer", "");
                            jumpTimer = (float)(rnd.NextDouble() * (jumpIntervalMax - jumpIntervalMin) + jumpIntervalMin);
                        }

                        await Task.Delay(TickMs, bot.CancelToken.Token);
                    }
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    retryCount++;
                }
            }

            // 재시도 전 잠시 대기 (Stop 명령이 아닐 때만)
            if (!bot.CancelToken.IsCancellationRequested && retryCount < maxRetries)
            {
                await Task.Delay(3000);
            }
        }

        // 루프 종료 (횟수 초과 또는 Stop)
        activeBots.TryRemove(bot.Id, out _);
        if (retryCount >= maxRetries)
        {
            Console.WriteLine($"[Bot {bot.Id}] 사망: 재시도 횟수 초과 ({maxRetries}회 실패).");
        }
    }

    static async Task<Uri> MatchMakeAsync(string playerId, CancellationToken token)
    {
        try
        {
            var content = new StringContent(
                JsonSerializer.Serialize(new { player_id = playerId }),
                Encoding.UTF8, "application/json");

            var res = await httpClient.PostAsync($"{MatchMakerBaseUrl}/api/find-game", content, token);
            res.EnsureSuccessStatusCode();

            var init = JsonSerializer.Deserialize<MatchInitResponse>(await res.Content.ReadAsStringAsync(token));
            if (init == null || string.IsNullOrEmpty(init.ticket_id)) return null;

            // 폴링
            for (int i = 0; i < 60; i++) // 최대 2분
            {
                if (token.IsCancellationRequested) return null;
                await Task.Delay(2000, token);

                var pollRes = await httpClient.GetAsync(
                    $"{MatchMakerBaseUrl}/api/ticket-status?ticket_id={init.ticket_id}&player_id={playerId}", token);

                if (!pollRes.IsSuccessStatusCode) continue;

                var status = JsonSerializer.Deserialize<MatchStatusResponse>(await pollRes.Content.ReadAsStringAsync(token));
                if (status != null && status.status == "MATCHED")
                {
                    return new Uri($"ws://{status.server_ip}:{status.server_port + 1000}/");
                }
            }
        }
        catch { return null; }
        return null;
    }

    static async Task SendJson(ClientWebSocket ws, string funcName, string param)
    {
        if (ws.State != WebSocketState.Open) return;
        string json = $"{{\"functionName\": \"{funcName}\", \"parameter\": \"{param}\"}}";
        await ws.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(json)), WebSocketMessageType.Text, true, CancellationToken.None);
    }
}