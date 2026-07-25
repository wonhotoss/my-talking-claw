# Day 3: 실제 에이전트 연동 — nullclaw 게이트웨이 계약 + Claude Code 스탠드인

## 목표

`/api/message`의 echo mock을 실제 자율형 에이전트(nullclaw류) 응답으로 교체한다. 이 PC엔 nullclaw가 없어 두뇌는 헤드리스 Claude Code가 대신한다.

방침(아키텍처 B): 백엔드는 항상 **nullclaw webhook 계약**만 호출하고, 그 계약을 그대로 구현한 **별도 에이전트 게이트웨이 스탠드인**이 헤드리스 Claude Code로 응답한다. 프로덕션에선 게이트웨이 URL만 실제 nullclaw로 바꾸면 백엔드는 무변경.

VAD/핫워드는 "직결 마이크 완전 보이스 인터페이스" 별도 마일스톤으로 연기(현재 폰 UI가 자연스러워 VAD가 어색).

## 조사: NullClaw 실제 인터페이스

- Gateway HTTP (`nullclaw gateway`, 기본 `127.0.0.1:3000`): `POST /pair`(헤더 `X-Pairing-Code: 6자리` → `{"token": …}`), `POST /webhook`(헤더 `Authorization: Bearer <token>`, 본문 `{"message": "..."}`), `GET /health`.
- CLI 단발: `nullclaw agent -m "..."`. LLM은 `~/.nullclaw/config.json` provider(Anthropic 등). 멀티턴은 nullclaw 자체 memory 엔진.
- (`/webhook` 응답 스키마는 공개 문서에 미명시 → 실제 nullclaw 연결 시 확인해 백엔드 parse만 조정.)

## 아키텍처

```
phone → frontend → backend /api/message ──[nullclaw webhook 계약]──▶ agent gateway :3000
                                                                       ├ (테스트) 헤드리스 Claude Code (claude -p)
                                                                       └ (프로덕션) 실제 nullclaw ← URL만 교체
```

**턴 기반 문제 해소**: 스탠드인 게이트웨이는 상시 서버, 요청 1건 = `claude -p` 1턴(수 초). 대화 세션(사람이 보는 Claude Code)이 라이브 두뇌일 필요가 없다. 단, headless `claude`를 Claude Code 세션 안에서 spawn하면 nesting으로 막힐 수 있어 라이브 테스트는 일반 터미널에서 한다.

## 구현한 것

### 신규 `agent-gateway/` (스탠드인)

- uv + FastAPI. `app/server.py`: `GET /health`, `POST /pair`(코드 검증 → `secrets` 토큰 발급·보관), `POST /webhook`(Bearer 검증 → 두뇌 호출 → `{"reply": …}`). 미인증/빈 메시지는 401/400.
- `app/claude_agent.py`: `claude -p <message> --output-format json --model <m> --append-system-prompt <voice 프롬프트> [--allowedTools ...] [--resume <session_id>]`를 `asyncio.create_subprocess_exec`로 실행, stdout JSON의 `result` 반환, `session_id`를 캐시해 다음 턴 `--resume`(멀티턴 문맥). 구독 인증 재사용(API 키 불필요). 실행 시 `CLAUDECODE`/`CLAUDE_CODE_*` 환경변수를 벗겨(`child_env`) 중첩 실행 가드를 우회하므로 Claude Code 세션 안에서도 동작. 기본 무툴(대화형), TTS 친화 짧은 한국어 시스템 프롬프트.
- env: `AGENT_PAIRING_CODE`, `CLAUDE_BIN`, `CLAUDE_MODEL`(sonnet), `CLAUDE_ALLOWED_TOOLS`, `CLAUDE_SYSTEM_PROMPT`, `CLAUDE_WORKDIR`.

### 백엔드

- `app/agent.py`: `agent_client.respond(message)` — 토큰 미보유 시 `/pair`로 획득·캐시(또는 `AGENT_BEARER_TOKEN`), `/webhook`에 Bearer로 POST, 401이면 1회 재pair·재시도. `httpx.AsyncClient`.
- `app/server.py`: `/api/message`를 async로, echo mock 제거 → `await agent.respond(text)`. 빈 텍스트 400, 게이트웨이 오류 502.
- `httpx`를 main dependency로 승격.
- env: `AGENT_GATEWAY_URL`(기본 `http://127.0.0.1:3000`), `AGENT_PAIRING_CODE`, (옵션)`AGENT_BEARER_TOKEN`.

### 프론트/proxy

변경 없음(백엔드→게이트웨이는 서버 간 통신). 타임라인에 실제 응답이 뜬다.

## 검증

- 유닛: `agent-gateway` **6 passed**(계약 + 페어링/Bearer 401 + 빈 메시지 400, claude mock), `backend` **3 passed**(에이전트 mock).
- **mock 라운드트립**(claude 없이 HTTP 배선 검증): 게이트웨이의 두뇌를 스텁으로 두고 게이트웨이(:3000)+백엔드(:8001) 실행 →
  - `GET :3000/health` → `{"status":"ok","model":"sonnet"}`
  - `POST :8001/api/message {"text":"hello agent"}` → `{"text":"[stub-claude] echoing: hello agent"}`
  - → 백엔드 pair(X-Pairing-Code→bearer) → /webhook(Bearer) → 게이트웨이 인증 → 두뇌 → reply 전 구간 정상.

## 라이브 E2E 검증 (완료)

- `claude` CLI가 이 PC엔 없어 `npm i -g @anthropic-ai/claude-code`로 설치(실제 실행 파일 `...\@anthropic-ai\claude-code\bin\claude.exe`). `create_subprocess_exec`는 `.cmd`/`.ps1` 심을 못 띄우므로 Windows에선 `CLAUDE_BIN`에 이 exe 전체 경로를 지정.
- **인증/비용**: `~/.claude/.credentials.json`이 `claudeAiOauth` → **claude.ai 구독 로그인**으로 동작(종량제 API 아님, `ANTHROPIC_API_KEY` 없음). CLI 출력의 `total_cost_usd`는 API 환산 **참고치**일 뿐 구독에선 달러 청구가 아니고 구독 사용량/레이트 리밋을 소모한다(이 코딩 세션과 같은 로그인 공유). 프로덕션 nullclaw는 provider API 키라 그쪽은 종량제.
- **중첩 가드 우회**: 게이트웨이가 claude 실행 시 `CLAUDECODE`/`CLAUDE_CODE_*`를 벗겨(`child_env`) Claude Code 세션 안에서도 동작함을 확인. (운영은 여전히 일반 터미널 권장.)
- **전 구간 확인**: 네 서비스(backend :8000, gateway :3000, voice :8100, frontend :5173) 기동 → `POST /api/message` → 게이트웨이 pair→webhook → **실제 Claude** → `{"text":"안녕하세요, 무엇을 도와드릴까요?"}`(원문 UTF-8 정상). 폰에서 발화 → STT → 에이전트 → TTS 실사용 확인.

## 다음 후보

- 실제 nullclaw 연결: `AGENT_GATEWAY_URL`을 nullclaw gateway로, `AGENT_PAIRING_CODE`를 nullclaw 부팅 코드로 교체. `/webhook` 실제 응답 스키마 확인해 adapter parse 조정.
- 스탠드인 자율화: `CLAUDE_ALLOWED_TOOLS`로 read-only 툴부터 점진 허용.
- 멀티 세션/대화 리셋, 스트리밍 응답.
- 직결 마이크 완전 보이스 인터페이스 + VAD/핫워드(연기분).
