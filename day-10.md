# Day 10: 두뇌 계층 — *claw 대체 (진행 중)

## 목표

[HANDOFF.md](HANDOFF.md) "Day 10 개요". *claw류의 핵심 가치(채널 통합)는 우리에게 없다. 음성 하나뿐인 기기의
두뇌에 **필요한 것만** 제공하는 얇은 계층을 정의하고, 첫 구현을 Claude Code 기반으로 한다.
날짜 2026-11-01. 쿨러 도착 전이라 **데스크탑 + 폰 클라이언트**로 개발·테스트하고, RPi 성능에
영향이 큰 변경은 따로 검토한다(사용자 결정).

## 출발점과 측정값

- 지금 두뇌는 `agent-gateway/app/claude_agent.py`: 턴마다 `claude -p --resume <id>` **프로세스를 새로 띄운다.**
- day-9 실측: 포스트→첫 소리 13.5s 중 **스폰 6.5s**(시원할 땐 2.6~3.1s), 에이전트 2.7s, 첫 문장 TTS 2.8s. 스폰이 가장 큰 덩어리이고 열·부하에 가장 민감하다.
- `--resume`은 게이트웨이 프로세스 수명 안에서만 기억이 산다. 타이머를 못 든다(턴 사이에 죽는다). 도구 정책은 `--allowedTools`뿐(승인 목록, 제한 아님). 페르소나·아웃바운드 채널(curl 명령과 토큰)이 **코드가 조립하는 시스템 프롬프트**에 박혀 있다.

## 설계 원칙 — [brain-memo.txt](brain-memo.txt) (사용자, 2026-11-01)

> 뇌 계층 설계의 핵심은 이 계층을 재사용 가능한 코드가 아니라 **유저 세팅**으로 두는 것이다. … 뇌 계층은 결국 뇌 인스턴스 자체의 설정과, 시스템이 접근할 호출 규약이다.

그래서 코드는 두 가지만 책임진다: **호스트**(뇌 인스턴스를 띄우고 살려 두는 것)와 **호출 규약**(시스템이 뇌를 부르고 뇌가 기기를 움직이는 방법). 말투·검열·가족 정보·기억과 예약의 방법·도구 정책은 전부 관리자가 Claude Code로 손보는 **뇌의 집** 안의 문서와 설정이다.

### 경계

| 어디에 | 무엇 | 비고 |
| --- | --- | --- |
| **뇌의 집** (디렉터리, 관리자 소유, 템플릿 [brain-home.example/](brain-home.example/)) | `CLAUDE.md`(페르소나·말하는 규칙·시간·기억·예약 규약·사용자 배경), `system-prompt.md`(선택), `.claude/settings.json`(모델·권한 모드·허용/금지 도구), `memory.md`, `schedule/*.json`, `session.json`(런타임) | Claude Code **프로젝트 디렉터리 그 자체**. 관리자가 거기서 `claude`를 열어 고친다 |
| **프로젝트 코드** (게이트웨이) | 호스트(`sdk_agent.py`: SDK 세션 상주, 재시작 시 resume, 시각 스탬프, interrupt), 호출 규약(webhook·NDJSON → 이벤트, 그대로), 스케줄러(`scheduler.py`: `schedule/`를 폴링해 때가 되면 `/api/push`), 집 위치·두뇌 선택 env | 뇌가 **무엇을** 아는지는 모른다. 파일 규약만 안다 |
| **문서** | 집의 레이아웃 규약(brain-home.example/README.md), 관리자 런북 | 코드 대신 설명이 담당하는 부분 |

### 단단한 층 / 무른 층 (사용자 질문 2에 대한 답)

"관리자가 문서를 어떻게 수정하건 모델이 무시하면 끝인가?" — 아니다. 규칙이 두 층이다.
- **단단한 층(코드가 집행, 모델이 무시할 수 없음)**: 어떤 도구가 존재하는지(`deny`는 정의 자체를 지운다), 승인 요청을 거부로 바꾸는 `dontAsk`, 쓰기를 `memory.md`·`schedule/*`로만 묶는 권한 규칙, 모델 선택, 무엇이 스피커로 나가는지(텍스트 블록만), 스케줄 파일 검증. 관리자가 `.claude/settings.json`에 적지만 **집행은 호스트**가 한다. 사용자 표현: "모델 밖의 프로그램 컨트롤 — 자율형 에이전트들이 박스 밖에서 하는 일".
- **무른 층(문서, 모델이 따르는 만큼)**: 말투, 가족 정보, 검열 문구, 파일 규약. 짧고 구체적일 때 잘 지키고, 길고 모순될 때 떨어진다.
- "어디까지 문서로, 어디부터 코드로"는 **관리자의 설정 항목**이다. 호스트는 강제 지점(권한 규칙, 훅)을 제공하고 관리자가 거기 무엇을 올릴지 정한다. 처음엔 문서로, 어긋나는 것만 코드로 올린다. 어긋남은 **보인다**: `schedule/`, `memory.md`, 게이트웨이 로그의 `notice`.

### "10분 후에 불러줘"는 실제로 (사용자 질문 1에 대한 답)

1. 오디오 루프 → STT → `POST /api/turns` → 백엔드 → 게이트웨이 → 상주 세션.
2. 게이트웨이가 질의 앞에 **현재 시각**을 붙인다(`[지금 2026-11-01 (토) 15:04]`). 모델은 시계가 없다. 호출 규약 = 코드.
3. 뇌가 CLAUDE.md의 규약대로 Write로 `schedule/2026-11-01T15-14-abc.json`에 `{"at": "...15:14:00", "say": "부르셨죠, 10분 지났어요."}`를 쓴다. 집 안 쓰기만 허용이라 통과. "네, 10분 뒤에 부를게요"라고 답하고 그 답이 스피커로.
4. 스케줄러가 5초마다 `schedule/`을 훑다가 15:14에 `POST /api/push`. `say`면 그대로 말하고(에이전트 홉 없음), `prompt`면 뇌에게 새 턴을 시켜 답을 말한다. 처리한 파일은 `schedule/done/`.
5. 파일이 깨졌으면 `schedule/failed/`로 옮기고 로그. 기기는 안 죽는다 — 이 스택에서 유일하게 "큰 소리로 건너뛰기"를 택한 곳(파일 저자가 모델이므로).

## 선택지 (구현체)

| | 지금: `claude -p` 스폰 | **Claude Agent SDK, 상주 세션** | Claude API (Messages + tool runner) | Managed Agents |
| --- | --- | --- | --- | --- |
| 인증 | 구독 로그인 재사용 | 같음 — SDK는 로컬 `claude` CLI를 돌린다 | **API 키, 종량제** | API 키, 종량제 |
| 턴당 지연 | 스폰 2.6~6.5s | **0** (프로세스 상주) | 0 | 0 + 세션 컨테이너 |
| 기억 | `--resume`, 프로세스 수명 | 세션 상주 + 재시작 시 `resume` | 직접 구현 | 서버가 보관 |
| 시간(예약) | 못 든다 | **게이트웨이가 상주하니 들 수 있다** | 직접 구현 | scheduled deployments |
| 도구 | `--allowedTools`(승인뿐) | settings.json `deny`(정의 제거) + `dontAsk` | 우리가 만든 것만 | 샌드박스 + MCP |
| 끼어들기 | 프로세스 kill | **`interrupt()`**, 세션 유지 | 요청 취소 | 세션 interrupt |
| RPi 비용 | 턴마다 node 기동 | **node 상주 ~150~250MB** | 없음(HTTP) | 없음 |
| 설정이 사는 곳 | env + 코드 | **집 디렉터리(CLAUDE.md·settings.json)** | 코드 | 서버 객체 |

**선택: Claude Agent SDK(Python) 상주 세션 + 뇌의 집.** 종량제 없이 지금 인증 그대로, 스폰이 사라지고, 예약과 끼어들기가 생기고, Claude Code 프로젝트 디렉터리가 곧 설정이라 메모의 원칙과 맞는다. 백엔드↔게이트웨이 계약은 그대로. 정책 주의: Agent SDK 문서는 "제3자에게 claude.ai 로그인을 제공하는 제품"을 금지한다. 본인 기기·본인 로그인인 지금은 `claude -p`와 같고, 배포 제품이 되면 API 키.

## 구현 (범위)

| 파일 | 역할 |
| --- | --- |
| `agent-gateway/app/sdk_agent.py` | 호스트. `ClaudeSDKClient` 상주, `BRAIN_HOME` 필수(CLAUDE.md 없으면 기동 거부), `setting_sources=["project"]`, `system-prompt.md` 있으면 그것, 없으면 최소 기본. `session.json` resume(실패 시 새 세션), 시각 스탬프, 번역, 취소 시 `interrupt()`+버퍼 비우기 |
| `agent-gateway/app/scheduler.py` | `schedule/*.json` 폴링 → `/api/push`. done/failed 이동. push 실패는 다음 틱 재시도 |
| `agent-gateway/app/server.py` | `AGENT_BRAIN=sdk\|cli`(기본 `cli` — RPi 성능 검토 뒤 전환), lifespan에서 스케줄러 기동 |
| `brain-home.example/` | 템플릿: CLAUDE.md, system-prompt.md, .claude/settings.json, memory.md, schedule/README.md, README.md(레이아웃·반영 시점·단단한/무른 층) |
| 테스트 | 번역·스탬프·세션 파일, 스케줄러(가짜 HTTP·임시 디렉터리) |

**RPi 성능 검토 대상(사용자 요청).** node 프로세스 상주(RSS 150~250MB 예상), 유휴 CPU. 데스크탑에서 재고 RPi 전환 전에 보고.

## 실측

### 데스크탑 스모크 — 상주 세션 (2026-11-01, SDK 0.2.176, CLI 2.1.293, sonnet, `dontAsk`, Bash/Write/Edit 제거)

| | 연결 | 턴 1 | 턴 2 ("방금 뭐라고 했지?") | 턴 3 ("오늘 날씨?") |
| --- | --- | --- | --- | --- |
| wall | **1.35s**(1회) | **2.25s** | **1.94s** | 6.30s |
| 첫 텍스트 | — | 2.22s | 1.93s | 6.28s |
| CLI 자체 duration_ms | — | 2211 | 1930 | 6290 |

- **wall ≈ duration_ms.** 스폰 오버헤드가 0이다(데스크탑 `claude -p`는 턴당 2.3~2.6s, RPi는 2.6~6.5s가 더 붙었다).
- 턴 2가 턴 1의 문장을 그대로 인용 → 세션 기억 동작.
- 턴 3: 이 스모크에선 WebSearch를 허용 목록에 안 넣어 "권한이 막혀 있다"고 답함 — `dontAsk`가 승인 요청을 거부로 바꾸는 것을 확인.

### 데스크탑 게이트웨이 실기 1 — 템플릿 집, 신뢰 안 된 폴더 (2026-11-01 16:30)

`AGENT_BRAIN=sdk BRAIN_HOME=<템플릿 복사본>`으로 게이트웨이를 띄우고 NDJSON 웹훅으로 3턴.

| 턴 | 첫 줄 | wall | 일어난 일 |
| --- | --- | --- | --- |
| "지금부터 2분 후에 나를 불러줘" | 3.47s (`notice Write`) | 5.75s | **Write가 거부됨** → "권한이 막혀서 못 불러드려요" |
| "내 이름은 원호야, 기억해 둬" | 2.07s (`notice Read`), 4.20s (`notice Edit`) | 5.89s | **Edit가 거부됨** → "메모리에 못 적었다, 새 세션이면 잊을 수 있다" |
| "내 이름이 뭐라고 했지?" | 1.70s | 1.72s | "원호라고 하셨어요" — 세션 기억은 동작 |

- 원인은 CLI stderr에 있었다: **"Ignoring 5 permissions.allow entries from .claude/settings.json: this workspace has not been trusted."** 신뢰되지 않은 폴더에선 settings.json의 `allow`가 조용히 무시된다(`deny`와 `defaultMode`는 적용). 그래서 `dontAsk`가 Write/Edit를 전부 거부했다.
- 규약 자체는 모델이 **정확히 따랐다**: 첫 턴에 바로 `schedule/`에 Write를 시도했고, 둘째 턴에 `memory.md`를 Read→Edit 하려 했다. 무른 층은 작동했고, 단단한 층이 (의도와 다른 이유로) 막은 것.
- 대응: ① 게이트웨이가 기동 때 `~/.claude.json`의 `projects[<집>].hasTrustDialogAccepted`를 확인하고 없으면 **기동 거부**(정확한 해결법을 메시지에) ② 집 README에 "복사 뒤 `claude`를 한 번 열어 신뢰 수락"을 필수 단계로. 신뢰 플래그를 게이트웨이가 대신 써 주는 건 하지 않는다 — 관리자의 CLI 설정을 코드가 건드리는 일이고, 설계상 그 한 번은 관리자의 몫이다.
- 턴 시간: 첫 줄까지 1.7~3.5s, 스폰 없음. 데스크탑 `claude -p` 대비 2~3s 빠르다.

### 데스크탑 게이트웨이 실기 2 — 신뢰된 집 `D:\projects\claw-home` (2026-11-01 16:35~16:37)

관리자(사용자)가 집에서 `claude`를 열어 신뢰를 수락한 뒤.

| 시험 | 결과 |
| --- | --- |
| "지금부터 2분 후에 나를 불러줘" (16:35) | `notice Write` 2.87s → "네, 2분 뒤에 부를게요" 3.97s. 집에 **`schedule/1637-call-user.json`** = `{"at": "2026-11-01T16:37:00", "say": "부르셨죠, 이 분 지났어요."}` — 시각 계산·파일명·형식 전부 규약대로 |
| "내 이름은 원호야, 기억해 둬" | `notice Read` 1.66s, `notice Edit` 3.23s → "네, 원호님. 기억해 둘게요" 4.09s. `memory.md`에 `- 2026-11-01: 사용자 이름은 원호다.` 한 줄 추가 |
| 게이트웨이 재시작 → "내 이름이 뭐라고 했지?" | `connected (resume=a3c5ce10…)` → "원호님이라고 하셨어요" 2.43s. **세션이 프로세스 재시작을 넘어 이어진다** |
| 끼어들기: 긴 답(1~20 세기) 요청 뒤 2초에 연결 끊기 → "방금 어디까지 셌어?" | 로그 `turn cancelled by the client; interrupting` → 후속 턴 2.0s에 "열일곱까지 세다가 열여덟 하는 중에 끊겼어요". **interrupt 뒤 세션 유지** |
| 스케줄러 (폴링 2s, push 싱크로) | **16:37:01**에 `POST /api/push {"source":"agent","prompt":null,"utterances":["부르셨죠, 이 분 지났어요."]}`, 파일은 `schedule/done/`으로 |

- "10분 후에 불러줘"의 전 과정이 설계대로 돌았다: 시각 스탬프 → 모델이 파일 규약을 따름 → 호스트가 파일을 집행.
- 끼어들기에서 2.2초 동안 받은 줄이 0개였다: 긴 답은 `AssistantMessage` 블록이 **완성될 때** 한 번에 온다. 문장 단위 선출력(`include_partial_messages`)은 첫 소리 지연에 직결되는 다음 항목(미룸, 아래).
- 데스크탑 함정: npm의 `claude.exe`가 오늘 **500바이트 텍스트 스텁**("claude native binary not installed")으로 바뀌어 있었다(사용자가 집에서 `claude`를 연 16:33에 자동 갱신된 것으로 보임). `cli_path`에 그걸 주면 WinError 216. **SDK 두뇌는 `CLAUDE_BIN`을 비워 두면 CLI를 스스로 찾는다** — 그게 동작했다. `cli` 두뇌(`claude_agent.py`)는 이 데스크탑에서 `CLAUDE_BIN`을 네이티브 바이너리로 다시 잡아야 한다.

### 데스크탑 전체 스택 + 폰 실사용 (2026-11-01 16:45~17:00, 사용자)

backend·voice(base beam 1)·tts(Piper, Docker)·frontend를 데스크탑에, 게이트웨이만 `sdk`. 폰은 day-7 방식(탭 녹음)이라 웨이크워드·말 끝 자동 판정은 없음 — 그건 RPi 오디오 루프의 몫.

| 항목 | 로그 |
| --- | --- |
| 턴 | `/api/turns` 6회(STT 5회), 게이트웨이 duration **1.8 / 2.4 / 2.8 / 2.9 / 3.3 / 6.7s**(6.7s는 3턴짜리 = 도구 사용 포함). 오류 0, `failed/` 0 |
| 예약 | "1분 후에 불러줘"(16:53) → `schedule/1654-call-user.json` `{"at":"…16:54:00","say":"원호님, 부르셨죠. 일 분 지났어요."}` → **16:54 스케줄러 발사 → `/api/push` 202 → 폰에서 재생.** 기억한 이름을 호출 문장에 썼다 |
| 기억 | `memory.md`에 "사용자의 집은 태형 아파트다" 추가 |
| 세션 | 같은 세션(`a3c5ce10`)이 실기 1·2·폰까지 이어짐. `api_ms` 누적 29s(세션 누적값, day-8과 같은 읽기 주의) |
| 사전 확인 | 백엔드 경유 "지금 몇 시야?" → 4.1s에 "지금은 오후 네 시 사십육 분이에요" — 숫자를 한국어로 읽는 규칙이 TTS 앞단에서 지켜짐 |

**RPi 성능 검토용 데스크탑 측정(상주 CLI 프로세스).** 게이트웨이가 띄운 `claude` 프로세스(16:46 시작): **RSS 224MB**, 15분·6턴 동안 **CPU 누적 5.8초** → 유휴 CPU는 사실상 0. 스폰 방식은 턴마다 node 기동이었으니 CPU는 오히려 줄고, 메모리 ~250MB가 상시 추가된다(RPi used 1.5GB + 0.25 → 4GB에서 여유). RPi 전환의 걸림돌은 메모리·CPU가 아니라 **aarch64 SDK 휠**과 **집 신뢰 수락** 두 가지 절차다.

사용자 체감(첫 소리·첫 문장 길이·숫자 읽기)은 아래 "남은 것"에 받아 적는다.

## 미룬 개선 (deferred)
- **문장 단위 선출력** — 현재: 텍스트 블록 완성 후 한 번에. 측정됨: 긴 답에서 2.2초 동안 0줄. 왜 안 했나: 백엔드 분할기와의 경계를 정해야 한다(델타를 어디서 문장으로 자르나). 착수 지점: `sdk_agent.py` `options()`에 `include_partial_messages=True`, `translate_message`에 `StreamEvent`(`content_block_delta`/`text_delta`) 누적 후 문장 경계에서 `reply`.
- **RPi 전환** — 현재 `AGENT_BRAIN` 기본 `cli`. 왜 안 했나: 사용자 요청(성능 영향 검토 먼저). 착수 지점: RPi에서 `uv sync`(SDK 휠 aarch64 확인), `~/claw-home`에 템플릿 복사 + `claude` 신뢰 수락, `rpi-up.sh` gateway 창에 `AGENT_BRAIN=sdk BRAIN_HOME=$HOME/claw-home`. 재야 할 것: node 상주 RSS·유휴 CPU, 턴 지연 before/after.
- **집의 세션 비대화** — 세션은 영원히 자란다(컨텍스트·비용 `api_ms` 누적). 왜 안 했나: 측정 전. 착수 지점: `ResultMessage.usage`를 로그에 더하고, 임계에서 `session.json` 삭제(새 세션; `memory.md`가 남는다) 또는 SDK 컴팩션 설정.
- **훅으로 올릴 단단한 규칙 후보** — 지금은 없음. 어긋남이 보이면: `PreToolUse`로 schedule JSON 형식 검사, `Stop`으로 마크다운 섞인 답 되돌리기.
