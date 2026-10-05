# HANDOFF — my-talking-claw

이 세션에서 이어서 작업하기 위한 인수인계. 마지막 갱신 2026-10-05.
**다음 세션은 RPi 위의 Claude Code다** — 이 문서는 그 세션이 day 8을 바로 시작할 수 있게 쓴다.

## 한 줄
마이크·스피커 없는 저성능 기기에 사는 자율 에이전트와 **음성으로 대화하는 인터페이스**. 지금은 폰 브라우저가 입·귀, 데스크탑이 서버 스택. 이걸 **RPi4 단일 기기(마이크+스피커+얼굴 화면)** 로 옮기는 중이고, 그 다음에 두뇌 계층(*claw 대체)을 얇게 다시 정의한다.

## 지금 상태 (확정된 것)
- **코드는 day-7까지 완료, 실기(RPi) 테스트는 0회.** 모든 실측 숫자는 Windows 데스크탑(Zen2 4코어) 값이다. RPi 값은 [platform-notes.md](platform-notes.md) §3의 **투영**뿐이다.
- 서비스 4개 + 프론트. 계약(`/voice/transcribe`, `/api/events`·`/api/turns`·`/api/push`, `/speak`)은 프론트 교체와 무관하게 유지.
  - `voice` :8100 — faster-whisper `base` int8. 한국어 `tiny`는 부족.
  - `tts` :8201 — **Piper `ko_KR-kss-medium`** (day-6에서 MeloTTS 교체 완료). RTF 0.057, RSS 252MB, 1코어로도 RTF 0.095. `Dockerfile.piper`는 aarch64에서 무수정 빌드(휠 확인됨). viseme는 `patch_voice_with_alignment`로 커버리지 100%.
  - `backend` :8000 — **SSE 이벤트 스트림**(day-7). `/api/message`는 삭제됐다. 트리거는 `POST /api/turns`, 에이전트 아웃바운드는 `POST /api/push`(토큰).
  - `agent-gateway` :3000 — nullclaw 웹훅 계약의 스탠드인. 두뇌는 `claude -p --output-format stream-json`, 세션은 `--session-id`/`--resume`, 백엔드엔 NDJSON으로 스트리밍.
  - `frontend` :5173 — Vite dev 서버(HTTPS, basic-ssl). 콘솔(`#/`)과 얼굴(`#/face`). 폰이 `/voice`·`/tts`·`/api`를 **전부 이 프록시를 통해** 부른다.
- **아키텍처: USB-RPi + 자체 스택 확정.** turnkey(ESP32+ESPHome+HA)는 안 간다(근거 [음성IO-로드맵.md](음성IO-로드맵.md) 2026-09-23 절).
- **최종 기기 RPi4 4GB**(Piper 전환으로 4GB면 충분, 메모리 예산은 platform-notes §3). 임시 x86 벤치 IdeaPad S210은 **후순위로 내려감**(RPi로 바로 간다).
- **ReSpeaker Lite Voice Kit 보유, 미플래시.** 보드 쪽 USB-C로 USB 펌웨어(`respeaker_lite_usb_xmos_v2.0.5.bin`) 플래시해야 UAC2 사운드카드가 된다. XIAO ESP32S3 쪽 USB-C와 혼동 금지. 동봉 스피커 임피던스(4Ω) 확인.
- 유닛 테스트 147개 통과(backend 59 / agent-gateway 19 / voice 5 / tts 64). 실행법은 [README.md](README.md) "검증".

## 3일 계획 (2026-10-05 확정)

| day | 한 줄 | 프론트 위치 | 서버 위치 |
|---|---|---|---|
| **8** | 현재 스택 그대로 **RPi 실기**. 폰은 그대로 클라이언트, 서버 스택만 RPi로. | 폰 (Vite는 RPi에서 서빙) | RPi |
| **9** | **ReSpeaker** 테스트 + 프론트를 **RPi로 이전** 시도(키오스크 + 마이크/스피커 직결). | RPi (폰은 보조 뷰어) | RPi |
| **10** | **LLM 레이어.** *claw류의 채널(telegram 등)은 우리에게 의미가 없다. *claw 기능을 대신하는 **더 얇은 계층**을 정의한다 — 구현체일 수도, 지침일 수도. 첫 구현은 Claude Code 기반. | — | — |

day 8은 이 저장소를 RPi에서 받은 뒤 **RPi 쪽 Claude Code가** 진행한다. 아래 런북이 그 세션의 출발점이다.

---

## Day 8 런북 — 서버 스택을 RPi로

### 목표
데스크탑을 루프에서 완전히 뺀다. 폰 → `https://<rpi-ip>:5173` 하나로 접속해 **말하고 → 받아쓰고 → 에이전트 → Piper → 폰 스피커**가 끝까지 돈다. 결과는 `day-8.md`에 **실측**으로 남긴다(투영이 아니라).

### 배치 (전부 RPi 한 대)
```
폰 브라우저 ──HTTPS──► RPi :5173 Vite(프록시)
                         ├─ /api, /health → :8000 backend
                         ├─ /voice        → :8100 voice (faster-whisper)
                         └─ /tts          → :8201 tts   (Piper, Docker)
                       backend ──NDJSON──► :3000 agent-gateway ──► claude -p
```
Vite를 RPi에서 띄우는 이유: 폰 쪽 코드가 상대경로(`/voice/transcribe`, `/tts/speak`, `/api/...`)로 **같은 오리진의 프록시**를 전제한다. 데스크탑 Vite를 남기고 프록시 타깃만 RPi로 돌리는 방법도 있지만 그건 데스크탑을 루프에 남기는 것이라 day 8의 목표에 어긋난다. `VITE_API_BASE_URL`은 `/api`만 바꾸고 `/voice`·`/tts`는 못 바꾸므로 반쪽이다.

### 전제 확인 (처음 10분)
```bash
uname -m; getconf PAGE_SIZE        # aarch64, 4096이어야 한다. 64비트 OS 필수.
free -m; df -h /                   # 4GB, 여유 디스크 수 GB
vcgencmd measure_temp; vcgencmd get_throttled   # 기준선. 0x0이 아니면 전원/냉각부터.
ss -ltnp | grep -E ':(3000|5173|8000|8100|8201)\b'   # 포트 충돌
which claude node npm uv docker; claude --version
```
**포트 3000 주의.** 이 RPi에는 이미 **진짜 nullclaw**가 돌고 있을 수 있다([agent-latency-notes.md](agent-latency-notes.md) §4가 그 기기의 `/proc/<pid>/cmdline`을 읽었다). nullclaw 게이트웨이의 기본 포트가 3000이라 스탠드인과 **충돌**한다. day 8은 스탠드인(`claude -p`)으로 간다 — nullclaw가 떠 있으면 스탠드인을 **3001**로 띄우고 백엔드의 `AGENT_GATEWAY_URL`을 그쪽으로. (nullclaw 자체를 두뇌로 쓰는 건 day 10의 비교 대상이지 day 8의 범위가 아니다.)

### 설치 순서
1. **`claude` CLI 로그인은 사람이 대화형 터미널에서 1회.** 헤드리스 세션은 OAuth를 못 한다. 기존 claude.ai 구독 로그인 재사용(API 키 불필요). npm 설치(`npm i -g @anthropic-ai/claude-code`) 또는 네이티브 설치 — 네이티브 바이너리면 턴당 150~200ms 이득(latency-notes §6). Linux에선 `CLAUDE_BIN` 기본값 `claude`로 충분.
2. **tts (Docker, 문서화된 경로):** `cd tts && docker compose up -d --build piper`. aarch64 휠은 확인돼 있고 수정 없이 빌드된다(platform-notes §4). 최초 빌드만 수 분. `curl :8201/health` → `{"engine":"piper"}`.
   - Docker가 없거나 데몬 RAM(0.3~0.8GB)이 아까우면 **네이티브 대안**: `uv sync` 후 `uv pip install "piper-tts==1.6.0" "onnx<2"`, `python -m piper.download_voices ko_KR-kss-medium --data-dir ./voices`, `python -m piper.patch_voice_with_alignment ./voices/ko_KR-kss-medium.onnx --output ./voices/ko_KR-kss-medium-aligned.onnx`, 그리고 `PIPER_MODEL`/`PIPER_CONFIG`/`TTS_ENGINE=piper`/`TTS_VISEME_MIN_SECONDS=0.02`를 compose 파일 값대로 env로 주고 `uv run uvicorn app.server:app --port 8201`. **패치 안 된 모델이면 `/speak`이 502로 거절한다** — 그게 정상이다. 이 경로를 쓰면 day-8.md에 적어라(pyproject에 piper가 없는 건 의도적이다).
3. **voice:** `cd voice && uv sync && WHISPER_MODEL=base uv run uvicorn app.server:app --host 0.0.0.0 --port 8100`. 첫 요청에 모델 다운로드(~150MB). ctranslate2 aarch64 휠 있음.
4. **agent-gateway:** README 표대로. `CLAUDE_WORKDIR`는 **빈 디렉터리**로 지정해라 — `--allowedTools`는 샌드박스가 아니라서(README 경고, CLI 2.1.220 실측) 에이전트가 **RPi 파일시스템에 Read/Write/Bash를 할 수 있다.** 홈 디렉터리에서 띄우지 말 것. Linux에선 `--reload` 금지 규칙이 Windows 전용이지만 그래도 붙이지 마라(코드 고치면 손으로 재시작).
5. **backend:** `AGENT_GATEWAY_URL`, `PUSH_TOKEN`(아무 값 — 없으면 `/api/push` 503) 주고 `--host 0.0.0.0 --port 8000`.
6. **frontend:** `npm install && npm run dev`. **`vite.config.js`가 `.ts`보다 먼저 로드된다** — 프록시를 바꿀 일이 있으면 둘 다. 폰에서 `https://<rpi-ip>:5173`, 자체서명 인증서 경고 통과(이미 데스크탑에서 하던 그대로).

전부 포그라운드로 띄우면 터미널 5개다. systemd/compose로 묶는 건 **day 8 범위 밖**(아래 "미룬 것"). tmux 세션 하나에 창 5개면 충분하다.

### 측정 — day-8.md에 남길 것 (전부 3회 이상, 콜드/웜 구분)
| 항목 | 방법 | 데스크탑 기준 | RPi 투영(platform-notes §3) |
|---|---|---|---|
| STT base int8 | `/voice/transcribe` 응답시간, 6~7초 발화 | 0.4~0.8s | 2.8~3.5s |
| Piper `/speak` | 서버 로그 또는 curl 시간, 5초 분량 문장 | 0.29s (RTF 0.057) | 1.4~2.3s (RTF 0.28~0.46) |
| TTS 콜드 스타트 | 컨테이너 기동→첫 `/speak` | 1.2s | 6~10s |
| 에이전트 홉 | 게이트웨이 stderr `[claude_agent] duration_ms= api_ms= ttft_ms=` | wall 3.5~7s | 동일(망 대기) |
| 턴 전체 | 폰 "받아쓰는 중"→첫 소리 | ~3s | **6~11s** |
| RAM | `free -m`, 턴 도중 | — | 소계 1.1~1.9GB |
| 열/스로틀 | `vcgencmd measure_temp`, `get_throttled` 턴 10회 뒤 | — | 팬리스면 여기서 갈린다 |

에이전트 홉은 [agent-latency-notes.md](agent-latency-notes.md) §5 런북 그대로 — 특히 **§5-3 "깨끗한 턴"**(재시작→첫 턴→같은 질문 3회)을 RPi에서 처음 돌리는 것이라, 그 문서의 "미룬 것" 1·3번(RPi 미측정, `MAX_ARG_STRLEN` 미확인)이 이 날 닫힌다. `stdin=DEVNULL` 적용 후의 프로세스 오버헤드 재측정(day-7 미룬 #9)도 같은 측정에서 나온다.

TTS를 1코어에 묶어도 실시간 이하인지(`--cpus=1`, platform-notes "파이에서 눈여겨볼 것")는 day 9의 얼굴 프레임 예산을 위해 **지금 재두면 좋다.** 선택.

### day-7이 남긴 수동 확인 — 폰+LAN이면 되므로 이 날 같이 친다
[day-7.md](day-7.md) "남은 확인" 1·2·3·4·5·7번: 첫 탭 오디오 언락, 턴 중 `#/face`↔`#/` 왕복, 탭 없이 예약 턴(`탭하면 들려드릴게요`), 알림 큐 중 녹음, object URL 누적, **kill된 턴 뒤 `--resume`**. 6번(Pi 키오스크)은 day 9.

### 함정 (실측된 것만)
- `/api/push`로 테스트할 때 **폰이 둘 이상이면 둘 다 말한다**(day-7 미룬 #4). 하나만 켜라.
- `--allowedTools`는 **권한 제한이 아니다.** 위 4번.
- 게이트웨이 `limit=4MB`·stderr 배수는 이미 들어 있다. 첫 "조사해줘" 턴에서 터지던 것들은 닫혔다.
- Piper가 영어를 못 읽는다("Claude Code"→"블러코도", day-6 미룬 #1). 실기에서 거슬리면 그때 치환 사전.
- 새 트리거가 활성 턴을 **선점**한다. 에이전트가 자기 턴 중에 push하면 자기를 취소한다(계약이 아니라 시스템 프롬프트가 막는다).

### 완료 기준
- [ ] 데스크탑 전원 꺼진 상태에서 폰→RPi 한 턴이 끝까지 돈다(소리까지).
- [ ] 위 측정표가 실측으로 채워졌다. 투영과 2배 넘게 어긋나는 항목은 원인 한 줄.
- [ ] `vcgencmd get_throttled`가 10턴 뒤에도 `0x0`.
- [ ] day-7 수동 확인 6개 중 통과/실패가 적혔다.
- [ ] `day-8.md`에 "미룬 개선" 절이 있다(아래 양식).

---

## Day 9 개요 — ReSpeaker + 프론트를 RPi로

**하드웨어 먼저.** ① 보드 쪽 USB-C로 USB 펌웨어 플래시 → `arecord -l`/`aplay -l`에 UAC2 장치. ② ReSpeaker를 **기본 입력이자 기본 출력**으로. XU316 AEC는 재생이 자기를 거쳐야(reference) 성립하므로 스피커 출력을 RPi 3.5mm나 HDMI로 내면 barge-in이 깨진다([hardware-notes.md](hardware-notes.md) "먼저 이해해야 할 3가지"). ③ 재생 중 녹음해 AEC가 실제로 자기 소리를 지우는지 — 이게 이 날의 핵심 실측이다(hardware-notes "미확인" 1번).

**프론트 이전의 1단계는 코드 0줄이다.** RPi Chromium 키오스크로 **지금 프론트**를 띄우면 `getUserMedia`가 ReSpeaker로 잡히고 `<audio>`가 ReSpeaker로 나간다. 즉 "탭해서 말하는 얼굴"은 그대로 된다.
```bash
chromium --kiosk --ignore-certificate-errors \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  https://localhost:5173/#/face
```
`--autoplay-policy`는 README에 있는 필수 플래그(예약 발화). `--use-fake-ui-for-media-stream`은 마이크 권한 프롬프트를 자동 승인한다(무인 부팅에 탭할 사람이 없다). 여기서 day-5·7이 미룬 **프레임 예산**(TTS 1코어 묶은 상태의 얼굴 60fps, 수다스러운 `notice` 때 리렌더 3배)을 잰다. Chromium 0.4~0.7GB가 메모리 예산에 들어온다.

**2단계가 설계 결정이다 — 오디오를 누가 소유하나.** 웨이크워드("누구야", openWakeWord)·VAD·덕킹은 상시 Python 루프가 자연스럽지만, 지금 재생은 브라우저에 있다. 선택지는 (a) 브라우저가 계속 오디오를 소유하고 Python 루프는 웨이크워드 감지만 해서 백엔드에 "녹음 시작" 이벤트를 넣는다, (b) Python 루프가 캡처·재생 전부를 가져가고 브라우저는 얼굴만 그린다(`utterance`를 받되 말하지 않는 뷰어 — day-7 미룬 #4 `role=viewer`가 정확히 이것). (b)가 최종 모양에 가깝지만 `/tts/speak` 호출과 viseme 타임라인을 Python에서 얼굴로 다시 넘겨야 한다. **day 9에서 결정하고, 결정 근거를 day-9.md에 남긴다.** 전환 중 폰은 보조 뷰어로 남긴다.

관련 착수 지점: day-7 미룬 #6(`POST /api/turns/speech` multipart — 트리거를 문 하나로), #4(`?role=`).

## Day 10 개요 — LLM 레이어 (*claw 대체)

**전제(사용자 결정):** *claw류의 핵심 가치는 채널(telegram 등) 통합인데 우리는 채널이 음성 하나뿐이라 그 부분이 무의미하다. 대신 **더 얇은 계층**을 정의한다. 구현체일 수도, 지침(프롬프트·CLAUDE.md·운영 규칙)일 수도 있다. 첫 번째는 Claude Code 기반.

**출발점은 이미 있다.** `agent-gateway/app/claude_agent.py`가 사실상 그 얇은 계층의 초안이다: 페르소나(`CLAUDE_SYSTEM_PROMPT`), 세션 연속(`--resume`), 아웃바운드 채널(`DEVICE_PUSH_*`로 시스템 프롬프트에 주입), 스트리밍 번역(`translate_line`). 부족한 것이 곧 day 10의 목록이다:
- **기억** — `--resume`은 게이트웨이 프로세스 수명 안에서만 산다. 재시작하면 대화를 잃는다. 세션 id 영속화냐, 별도 메모리(파일/CLAUDE.md)냐.
- **시간** — 예약은 에이전트 책임으로 정했지만(day-7 결정 4) `claude -p`는 턴 사이에 죽어 **타이머를 못 든다.** OS `at`/cron 위임, 상주 루프, Agent SDK 중 어디에 둘 것인가. 이건 day-7 미룬 #1과 같은 질문이다.
- **도구 정책** — `--allowedTools`는 승인 목록일 뿐이다. 무엇을 **`--disallowedTools`** 로 막고, `CLAUDE_WORKDIR`에 무엇을 둘 것인가(에이전트의 "집").
- **발화 규율** — 짧은 구어체·마크다운 금지는 프롬프트 두 문장이 전부다. 숫자·영어 읽기(Piper 약점)까지 프롬프트가 책임질지, TTS 앞단 치환이 책임질지.
- **계약** — 백엔드↔게이트웨이의 nullclaw 웹훅 계약(`/pair`, `/webhook`)을 **유지할 이유가 남는가.** 채널 통합을 버리면 nullclaw 호환이 목적이던 이 계약도 재검토 대상이다. 유지하면 nullclaw/openclaw를 두뇌로 꽂아 비교할 수 있고, 버리면 홉 하나가 준다.
- **비용/지연** — 턴당 프로세스 스폰 2.3~2.6초(stdin 정정 후 재측정 필요, day 8). 상주 세션(Agent SDK)으로 가면 사라지지만 그건 "구현체" 쪽 선택이다.

산출물은 코드일 필요가 없다. **"이 계층이 제공해야 하는 것" 목록과 각 항목의 선택 + 근거**가 day-10.md에 있으면 된다. 구현은 그 다음.

---

## 저장소 / RPi로 받기
- 로컬 git(`main`), **원격 미부착.** RPi가 받으려면 사용자가 ① 원격을 붙여 푸시하거나 ② RPi에서 데스크탑으로 `git clone ssh://…`(Windows OpenSSH) 하거나 ③ `git bundle`을 복사한다. 어느 쪽이든 **사용자가 대화형 터미널에서.**
- 이번 세션의 HANDOFF 갱신은 **커밋하지 않았다.** `git add HANDOFF.md && git commit -m "HANDOFF: day 8~10 계획 + RPi day-8 런북"`.
- `.venv`·`node_modules`·`dist`는 무시 대상이라 RPi에서 `uv sync`/`npm install`을 다시 한다. `frontend/*.tsbuildinfo`가 추적되고 있는 건 노이즈다(미룬 것).
- 테스트는 RPi에서도 `uv run pytest`로 전부 돈다(엔진 없이 통과하도록 돼 있다).

## 미룬 것 (이번 세션)
- **systemd/compose로 5개 프로세스 묶기.** 현재: 터미널 5개. 왜 안 했나: day 8은 "돌아가는가·얼마나 걸리나"가 질문이고 배포 형태는 day 9 이후 결정(오디오 소유자에 따라 프로세스 구성이 바뀐다). 착수 지점: `tts/docker-compose.yml`에 나머지를 서비스로 얹거나, `deploy/*.service` 4개.
- **`day-8.md` 골격을 미리 만들지 않았다.** RPi 세션이 실측하며 쓰는 게 맞다. 양식은 day-6/7과 같게 — 목표 / 측정(표) / 조용히 깨질 뻔한 것 / 검증 / 남은 확인 / **미룬 개선(현재·왜 안 했나·착수 지점)**.
- **vite.config.js 이중 추적**(day-6 미룬 #7)과 **tsbuildinfo 추적**은 그대로 뒀다. 범위 확장이라 물어보고 할 일.
- day-6·7의 미룬 목록은 그 문서들에 그대로 살아 있다. 이 문서에 다시 옮기지 않았다 — 두 벌이 되면 어긋난다.

## 서브트랙: clova-track/ (대기)
클로바 프렌즈를 루팅해 음성 프론트엔드로 재활용하는 대체 하드웨어 트랙(구 super-clova, 2026-09-23 병합 → [clova-track/MERGE.md](clova-track/MERGE.md)). **로드맵 2·3단계, 현재 대기.** 착수 전 [clova-track/발판확보-런북.md](clova-track/발판확보-런북.md) 단계 1.5(비파괴 BootROM 지문) 선행. 막힌 지점: 프렌즈 SoC 미확정·발판 미확보. **이번 세션에서는 건드리지 않았고, day 8~10 범위에도 없다.**

## 문서 지도
| 문서 | 내용 |
|---|---|
| [overview.md](overview.md) · [milestone-1.md](milestone-1.md) · day-1~7.md | 초기 설계·경과. day-6 = Piper 전환, day-7 = 이벤트 스트림 |
| [README.md](README.md) | 각 서비스 실행법·env 표·이벤트 계약·검증. **명령은 여기가 원본** |
| [agent-latency-notes.md](agent-latency-notes.md) | 에이전트 홉 분해·기각된 가설·지연 조사 런북(§5). day 8 측정이 이 문서의 "미룬 것"을 닫는다 |
| [platform-notes.md](platform-notes.md) | RPi4 vs IdeaPad, Piper 실측, RPi **투영**(§3)·빌드 블로커(§4) |
| [hardware-notes.md](hardware-notes.md) | 마이크/스피커 비교·확정 BOM·AEC/barge-in 원리 |
| [음성IO-로드맵.md](음성IO-로드맵.md) | 3단계 로드맵 + 2026-09-23 최종 아키텍처 결정 |
| [clova-track/](clova-track/) | 클로바 리버싱·분해 트랙 (+ [MERGE.md](clova-track/MERGE.md)) |
