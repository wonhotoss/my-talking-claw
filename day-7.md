# Day 7: 단일요청-단일응답을 버렸다 (트리거 → 서버 드리븐 이벤트)

## 목표

프론트-백 계약을 **요청 → 응답**에서 **트리거 → 이벤트 연속**으로 바꾼다.

지금까지의 계약은 `POST /api/message {text}` → `{text}`였다. 세 홉이 전부 직렬이고
스트리밍은 리포 전체에 없었다(`WebSocket|EventSource|StreamingResponse|getReader` 전수 grep →
히트 0). 이 계약으로는 실제 대화에서 예상되는 네 상황이 **표현 자체가 불가능하다**.

| 상황 | 왜 불가능했나 |
| --- | --- |
| 예약작업 (7시에 깨워줘) | 서버가 말하려면 클라이언트 요청이 떠 있어야 한다. 7시엔 아무 요청도 없다. |
| 멀티턴 응답 (조사하겠습니다 → 발견했습니다 → 결론은) | 응답이 하나뿐이다. 중간보고를 담을 자리가 없다. |
| 긴 응답 예상 (기다려 주세요) | 같음. 먼저 보낼 채널이 없다. |
| 문장별 TTS 스트리밍 | 첫 문장이 전체 답변과 같은 시점에 도착한다. |

`agent-latency-notes.md:213`이 이 지점을 이미 미뤄뒀다 — *"단일 응답 계약을 스트리밍으로 바꾸는
일이라 nullclaw 쪽과 같이 정해야 하고, AGENTS.md가 '파이프라인/아키텍처 변경은 먼저 물어라'라고
한 범위에 해당한다. 착수 지점: `app.tsx`의 `run_turn`."* 이번이 그 착수다.

## 결정 네 개

| 결정 | 선택 | 근거 |
| --- | --- | --- |
| 전송 | **SSE** | 재접속·`Last-Event-ID`가 브라우저 기본 기능. 오디오는 지금도 base64 JSON이라 바이너리 프레임 이득이 0. **모든 새 경로를 `/api` 아래** 두면 `vite.config.ts`/`.js` 이중 수정 함정(day-6 미룬 #7)을 아예 안 건드린다. |
| 게이트웨이 | **`Accept: application/x-ndjson` 콘텐츠 협상** | 백엔드가 응답 Content-Type만 보고 분기 → 설정도 capability probe도 캐시된 플래그(=파생 상태)도 없다. 진짜 nullclaw가 구현 안 해도 그대로 동작. |
| 문장 분할 | **블록 단위부터** | 델타(`--include-partial-messages`)는 중복발화 함정이 있어 미뤘다. |
| 예약작업 | **채널만. 스케줄링 책임은 에이전트.** | 기기는 "말해라"만 노출한다. 잡 스토어·의도 추출·캐치업은 안 만든다. |

## 측정 — 먼저, 지연의 2~3초가 stdin이었다

`stream-json` 실측을 하려고 CLI를 처음 직접 돌렸을 때 stderr에 이게 나왔다.

```
Warning: no stdin data received in 3s, proceeding without it.
If piping from a slow command, redirect stdin explicitly: < /dev/null to skip, or wait longer.
```

같은 프롬프트를 stdin만 바꿔 재보면:

| stdin | 실측 | 경고 |
| --- | --- | --- |
| 상속(터미널) | 6746ms, 6887ms | **매번 나온다** |
| `< /dev/null` | 5314ms, 3619ms | 없음 |

`/dev/null` 쪽 분산이 큰 건 모델 지연 노이즈지만, 상속 쪽 **최소값이 devnull 최대값보다
크다**. CLI가 스스로 원인을 말해주므로 메커니즘도 명확하다 — 파이프로 들어올 stdin을 3초
기다린다.

기존 `claude_agent.py`는 `create_subprocess_exec(stdout=PIPE, stderr=PIPE)`만 지정하고
**`stdin`을 지정하지 않았다.** 즉 uvicorn의 stdin을 그대로 상속한다. 이제 명시적으로
`stdin=DEVNULL`을 준다.

**정직하게 한계**: 위 표는 Git Bash TTY stdin 기준 직접 측정이다. 구 게이트웨이가 실제로 이
3초를 매번 냈는지는 uvicorn을 어떻게 띄웠는지에 달려 있어 이 숫자로 단정할 수 없다. 확실한
건 (1) 이제 상속 환경에 의존하지 않는다는 것과 (2) `agent-latency-notes` §의 "순수 프로세스
spawn/init 2.3~2.6초"가 **이것과 같은 크기**라서 다시 재볼 값이라는 것이다.

## `stream-json`이 실제로 주는 것

`claude -p --output-format stream-json --verbose`의 줄 종류를 실측했다(CLI 2.1.220).
`--verbose`는 **필수다** — 없으면 `Error: When using --print, --output-format=stream-json
requires --verbose`로 즉시 죽는다.

문서에 없는 줄이 세 종류 나왔다: `rate_limit_event`, `system/thinking_tokens`, 그리고
assistant 메시지 안의 `thinking` 블록. **`thinking`은 절대 말하면 안 된다.**

툴을 쓰는 턴의 시퀀스가 정확히 우리가 원한 모양이었다.

```
assistant text  "probe 디렉터리를 찾아서 파일 개수를 세어볼게."   ← 중간보고
assistant tool_use  Bash                                        ← 보여주기만
user      tool_result  "2"
assistant text  "probe 디렉터리에는 파일이 2개 있어."             ← 결론
result    ttft_ms=3713  duration_ms=9399  num_turns=3
```

그리고 `result.result`가 **마지막 assistant 텍스트를 그대로 복제한다.** 둘 다 흘리면 기기가
마지막 문장을 두 번 말한다. `result`는 `done`으로만 번역한다.

### 단일 답변에서는 거의 안 당겨진다 (예상대로)

트리거 → 첫 utterance → `turn_ended`를 이벤트 스트림에서 직접 재봤다. 각 3회.

| 프롬프트 | 첫 utterance | turn_ended | 앞당긴 시간 |
| --- | --- | --- | --- |
| 단일 답변("수도는?") | +3511 / +4017 / +4546 ms | +4127 / +4634 / +5166 ms | **~0.6s** |
| 멀티턴(툴 사용) | +3947 / +4637 / +4699 ms | +6939 / +7147 / +7713 ms | **~2.5–3.0s** |

블록 단위 스트리밍의 이득은 **지연이 아니라 구조**라는 계획의 전제가 그대로 확인됐다. 단일
답변은 생성이 끝나고 블록 하나가 통째로 오므로 0.6초뿐이다. 멀티턴은 첫 문장을 **2.7초 먼저**
말할 수 있다. 여기에 Piper 합성 ~0.45초를 더하면 그게 실제 첫 소리 시점이다.

## 아키텍처

```
브라우저 ──GET  /api/events ─────────────► 계속 열림. 서버가 밀어넣는다.
        ──POST /api/turns {text, turn_id} ──► 202. 본문에 답이 없다.
        ──POST /api/turns/{id}/cancel ──────► 202. barge-in.
        ──POST /voice/transcribe :8100 ─────► 그대로 (무변경)
        ──POST /tts/speak :8201 ────────────► utterance 하나당 한 번

에이전트 ──POST /api/push (X-Push-Token) ──► 기기가 말한다. 대기 요청 없음.

backend ──httpx, Accept: application/x-ndjson──► gateway /webhook
gateway ──claude -p --output-format stream-json --verbose──► claude
```

기기는 **싱글턴**이다. 스피커 하나, 얼굴 하나, 대화 하나. 멀티테넌트 서버가 아니라 네트워크
포트가 달린 입이다. 그래서 버스 하나에 구독자 N명이 같은 걸 본다 — 키오스크와 폰은 한 기기를
보는 두 창이다.

### `utterance` vs `notice`가 스키마의 핵심선

"조사해 보겠습니다"는 에이전트가 **실제로 한 말**이므로 `utterance`(말한다). 회색 상태줄
"Bash"는 `notice`(말하지 않는다). 클라이언트 오디오 파이프라인이 정확히 이 분기를 탄다. 흐리면
안 된다.

### 문장은 게이트웨이와 백엔드 둘 다 자른다

- **게이트웨이**는 가진 입도(오늘은 assistant 블록당 하나)로 `reply`를 낸다. 입도를 약속하지 않는다.
- **백엔드**는 들어온 모든 `reply`를 문장으로 쪼개 `utterance` 하나씩 낸다.

> 발화의 입도는 백엔드와 클라이언트의 계약이고, 전달의 입도는 게이트웨이와 백엔드의 계약이다.
> 두 관심사, 두 손잡이, 합치지 말 것.

게이트웨이만 자르면 열화가 절벽이다 — 통째로 응답하는 nullclaw는 5문장 `utterance` 하나,
`/tts/speak` 한 번, 파이프라이닝 0. 백엔드에서 자르면 **게이트웨이가 스트리밍하든 안 하든
클라이언트 동작이 동일**하고, 게이트웨이의 더 잘게 쪼갠 입도는 타이밍만 개선한다.

## 구현한 것

### `agent-gateway/app/claude_agent.py` — `stream()`과 순수 번역기

`translate_line(payload) -> list[agent_line]`을 **순수 함수로 뺐다.** 서브프로세스 없이
캡처한 픽스처로 번역 전체를 테스트할 수 있다. `respond()`는 `stream()`을 모아 join하도록
재구현해서 JSON/NDJSON 두 경로가 코드 하나를 공유한다(day-6의 "코드는 하나" 논리).

세션 id를 **여기서 생성**한다. 첫 턴은 `--session-id <uuid>`로 만들고 이후 `--resume <uuid>`.
첫 프로세스가 뜨기 전에 id가 알려지므로 `/proc/<pid>/cmdline` 런북이 턴 1부터 동작한다. 실측에서
두 턴이 같은 세션(`a39264e9-…`)을 쓰는 게 확인됐다.

`limit=4MB`를 줬다. asyncio `StreamReader`의 기본 줄 한계가 64KiB인데, 파일을 담은
`tool_result`는 그걸 넘는다 — **짧은 테스트 프롬프트에선 절대 안 나오고 첫 진짜 "조사해줘"에서
나온다.** stderr는 별도 태스크로 배수한다(stdout을 한 줄씩 읽는 프로세스의 stderr 파이프가
차면 데드락이다).

### `agent-gateway/app/server.py` — 콘텐츠 협상

`Accept: application/x-ndjson`이면 `StreamingResponse`, 아니면 기존 JSON 본문. 본문 모양이
둘이라 `response_model`을 뗐다. 실패는 **줄로** 온다 — 그 시점엔 200이 이미 와이어에 나가 있다.
`CancelledError`는 일부러 잡지 않는다(끊긴 클라이언트가 서브프로세스 kill까지 전파돼야 한다).

### `backend/app/events.py` + `utterance.py` (신규, 순수)

`viseme.py`/`speech.py`가 확립한 패턴 — HTTP도 I/O도 없이 어디서나 유닛 테스트 가능.
이벤트 모델은 **모든 필드 필수**(pydantic 기본값 없음)라 TS 유니온이 total 해지고 클라이언트
switch가 exhaustive해진다.

링 버퍼(256)를 넣은 진짜 이유는 잃어버린 문장이 아니다: **마지막 `utterance`와 `turn_ended`
사이가 끊기면 클라이언트가 영원히 "말하는 중"에 갇힌다.** 멈춘 키오스크가 최악의 실패다.
게다가 `id:` 라인은 계약 모양이라 나중에 넣는 건 와이어 브레이크다. 느린 구독자는 큐(64)가
차면 **끊는다** — 브라우저가 `Last-Event-ID`로 재접속해 재생한다. 시끄러운 실패, 자가 치유.

`split_utterances`는 `piper.py::sentence_pattern`을 **의도적으로 복제**한다. 두 서비스는 독립
배포되고, 답하는 질문도 다르다 — Piper는 한 `/speak` 안의 합성 조각 경계를 정하고, 여기서는
`/speak`를 언제 따로 부를지(=언제 소리가 나기 시작할지)를 정한다.

### `backend/app/turns.py` (신규)

**정책: 새 트리거가 활성 턴을 취소하고 시작한다.** 최신 의도가 이긴다 — 음성 기기의 동작이고
barge-in이 공짜로 나온다. 큐는 안 만들었다.

`start_*`는 다음 턴을 띄우기 전에 **이전 턴의 죽음을 완료까지 await** 한다. 이유는 파이썬 필드
경쟁이 아니라 **두 `claude --resume <같은 id>`가 같은 세션 트랜스크립트 파일을 동시에 쓰는
것** — 에러가 아니라 조용한 손상이다. 게이트웨이에도 `asyncio.Lock`을 걸었다.

SSE 클라이언트가 턴 중간에 끊겨도 **턴은 계속 돈다.** 턴은 구독자가 아니라 기기가 소유한다.

`start_utterances`는 verbatim 경로다 — 에이전트 홉 없음, 서브프로세스 없음, 토큰 소모 없음.
예약 알림이 여기로 들어온다.

### `backend/app/server.py` — 엔드포인트 넷, `/api/message` 삭제

`stream_hello`는 **`id:` 없이** 보낸다. 연결 스코프이므로 시퀀스를 먹거나 클라이언트의
`Last-Event-ID`를 덮어써선 안 된다.

**`Last-Event-ID`가 없으면 재생하지 않는다.** 새 접속을 "0부터 재생"으로 취급하면 새로고침마다
지난 턴을 다시 말한다. 이건 실측으로도 확인했다.

`/api/push`는 `PUSH_TOKEN` 미설정 시 **503에 명시적 메시지**. 조용히 받거나 조용히 거절하지
않는다. `/api/turns`와 분리한 이유는 페이로드가 달라서가 아니라 **인증 경계가 달라서**다.

### 프론트엔드

`event_stream.ts`(신규) — 연결은 **모듈 스코프**다. `app_shell`은 턴당 여러 번 리렌더하므로
모듈 레벨 소스가 구조적으로 teardown 면역이다. `read_stream_status()`는 `readyState`에서
**계산**한다(중복 연결 상태가 없다). day-5가 해시 라우트를 `useState` 대신 subscription으로 만든
것과 같은 논리.

`speech_queue.ts`(신규) — 이번 작업의 핵심. **prefetch 스케줄러를 만들지 않았다.** `push`가
즉시 합성을 걸고 promise를 보관한다. Piper 450ms인데 utterance는 수 초 간격으로 오므로 깊이
제한이 걸릴 일이 거의 없고, **promise 자체가 동기화 원시**라 "N+1이 N보다 먼저 끝남"이 특수
케이스가 아니라 정상 케이스가 된다 — 직렬 플레이어가 이미 resolve된 promise를 await할 뿐이다.
틀릴 순서 로직도, 파생 스케줄링 상태도 없다.

`app.tsx` — `activity_state`를 셋으로 쪼갰다(`mic_phase` / `speech_phase` / `turn_label`).
이제 진짜로 동시에 일어나기 때문이다: 오디오가 재생 중인데 에이전트는 여전히 생각 중이고,
비요청 발화가 정지 상태에서 시작한다. `turn_ended`는 **큐가 아니라 라벨만** 지운다 — 서버에서
턴이 끝날 때 마지막 발화들은 보통 아직 재생 중이다. 상태를 쪼갠 이유가 정확히 이것이다.

`accepted_turn_ref` 하나가 "취소된 턴 집합"을 대체한다. barge-in 후 `null`이므로 중단된 턴의
낙오 `utterance`는 전부 폐기·로깅되고 합성되지 않는다. **서버가 턴을 직렬화하기 때문에만** 옳다.

## 조용히 깨질 뻔한 것들

**1. 시작도 못 한 턴은 끝나지도 않았다.** `start_prompt`가 `create_task` 직후 리턴하는데, 루프가
그 태스크를 한 번도 실행하기 전에 `cancel()`이 오면 **코루틴 본문이 아예 실행되지 않는다.**
`try/finally`에 있던 `turn_ended` 발행이 통째로 건너뛰어진다. 클라이언트는 영원히 "생각하는
중"에 갇힌다 — 링 버퍼로 막으려던 바로 그 실패를 코드가 직접 만들고 있었다. 테스트가 잡았다
(`turn_ended` 리스트가 비어서 IndexError). `create_task` 뒤에 `await asyncio.sleep(0)` 하나로
첫 suspension point까지 밀어준다. 전용 테스트를 남겼다.

**2. `--allowedTools`는 권한을 제한하지 않는다.** `CLAUDE_ALLOWED_TOOLS=""`(=운영 기본값)로
`claude -p`를 돌렸는데 `system/init`이 툴 **33개 전부**를 available로 보고하고 `Read`가
permission denial 0건으로 실제 실행됐다. `--allowedTools "Glob Read"`를 줘도 `Bash`가 돌았다.
즉 **지금 이 게이트웨이의 에이전트는 이미 Read/Write/Edit/Bash를 쓸 수 있다.** docstring의
"비우면 대화형(무툴)"은 **틀린 설명이었다.** day-7 변경과 무관하게 원래 그랬다. 실제로 막으려면
`--disallowedTools`가 필요하다. 코드와 README에 경고로 남겼다.

**3. `result.result`가 마지막 문장을 복제한다.** 그대로 흘리면 기기가 마지막 문장을 두 번
말한다. 회귀 테스트로 고정했다(`replies.count(...) == 1`).

**4. `thinking` 블록.** sonnet은 assistant 메시지에 `thinking` 블록을 넣는다. `type == "text"`만
거르지 않으면 생각을 소리 내어 읽는다.

**5. 발화 사이 track swap 순서.** `audio.pause()` → `src` → `track` → `play()` 순서가
load-bearing이다. 역순(옛 오디오가 도는 중에 track 먼저)이면 `advance_span_cursor`가 옛
`currentTime`을 새 track의 **마지막** 스팬으로 클램프해서 다음 발화의 마지막 자막이 번쩍인다.

**6. `unlock_audio`가 살아있는 엘리먼트를 가로챌 수 있다.** 새 실패 모드: 탭이 한 번도 없었는데
비요청 발화가 재생될 수 있다(데스크탑, 그리고 자동재생을 허용한 Pi 키오스크). 첫 탭이 살아있는
end-waiter 아래로 `src`를 무음 WAV로 바꿔치기하면 그 리스너는 영원히 안 켜지고 **큐가
데드락**한다. `!audio.paused` 가드를 넣었다.

**7. `set_hold`가 재생 중인 클립을 pause하면 데드락이다.** `pause()`는 `ended`를 내지 않으므로
`run()`이 영원히 `wait_for_playback`에 앉는다. 홀드는 **다음** 발화의 시작만 막는다 — 그거면
충분하다. `handle_mic_click`이 마이크를 열기 전에 이미 cancel한다.

**8. `speech_pending`을 의존성 배열에 넣어야 한다.** rAF 루프는 오래 사는 클로저라 dep이 아닌
prop은 마운트 시점에 얼어붙고, 버그가 간헐적으로 보인다.

**9. `uvicorn --reload`가 게이트웨이를 완전히 망가뜨린다 (Windows).** 실기 테스트 중에 잡혔다.
uvicorn은 `--reload`나 `--workers`를 쓰면 Windows에서 **Selector** 루프를 고르는데, 거기서는
`asyncio.create_subprocess_exec`가 지원되지 않는다. 게이트웨이는 그걸로 `claude`를 띄우므로 모든
턴이 즉시 실패한다.

증상이 특히 나빴다: `NotImplementedError()`는 **메시지가 비어 있어서** 에러 이벤트가
`agent failure: ` 로 끝났다. 원인을 하나도 알려주지 않는다. 그래서 예외 메시지에 **클래스 이름을
넣도록** 고쳤다 — 이제 `agent failure: NotImplementedError:` 로 나오고, 그 이름 하나로 바로
찾아진다. 테스트로도 고정했다.

같은 코드에서 `--reload`만 빼면 정상 동작한다(턴 완료, `duration_ms=6745`). 백엔드는 HTTP만 하니
`--reload`가 안전하다. README에 경고로 남겼다.

**10. `python`이 Python 3.3이었다.** `/c/Python33`이 PATH에서 `/c/Python313`보다 앞에 있어서
f-string이 SyntaxError로 죽고 `pathlib` import가 실패했다. 이 리포 코드와 무관한 로컬 환경
문제지만, 스크립트로 뭘 검증할 때 헛발질하게 되니 적어둔다.

## 리뷰에서 나온 것들 (구현 후 정리)

동작하는 걸 확인한 뒤 코드 품질 리뷰를 돌렸고, 그중 셋은 **설계를 바꿨다.**

**1. "최신 의도가 이긴다"가 서버에만 있었다 — 정작 오디오는 클라이언트에 있다.**

`turn_runner.preempt`는 활성 턴을 취소한다. 그런데 턴의 utterance는 **기기가 말을 마치기 훨씬
전에** 전부 발행되고, verbatim 예약 발화는 아예 한꺼번에 발행된다. 즉 서버는 그 턴을 이미
끝난 것으로 보는데 클라이언트 큐에는 수 초 분량의 오디오가 남아 있다. 그 상태에서 새 턴이
시작되면 **선점이 아무것도 못 하고**, 알림 남은 문장들이 먼저 다 나온 뒤에 새 답이 붙었다.

처음엔 `start_utterances`를 다른 턴과 같은 task 경로로 통일해서 고치려 했는데(아래 2번), 그건
이 문제를 못 고친다 — `emit`에 await이 없어서 task가 즉시 끝나므로 `preempt`는 여전히 "이미
끝난 task"를 본다. **정책은 오디오가 있는 곳에서 집행돼야 한다.** `turn_started`가 이미
adopt한 턴이 아니면 클라이언트가 큐를 비운다. 우리 자신의 턴은 POST 전에 adopt하므로 id가
일치해서 안 건드린다.

헤드리스로 확인: 4문장 알림을 넣고 3초 뒤(2개 재생 중) 새 턴을 넣으면, 이후 재생되는 클립이
**1개**다(남은 2개가 버려졌다). 고치기 전엔 3개였다.

**2. 턴 생명주기가 두 벌이었다.** `start_utterances`가 `turn_started`/`utterance`/`turn_ended`를
인라인으로 발행해서 task를 만들지 않았다. 그래서 `snabpshot`과 `task`가 서로 다른 턴을 가리키는
상태가 **설계상** 생겼고, `active`의 "task에서 파생하니 어긋날 수 없다"는 주장이 깨졌다. verbatim
턴은 `cancel()`도 안 되고 `stream_hello.active_turn`에도 안 보였다. 이제 두 종류가 같은
`start → run → end` 경로를 탄다. 에이전트 홉이 없다는 것과 턴이 아니라는 건 다른 얘기다.

**3. `await asyncio.sleep(0)`를 지웠다.** "조용히 깨질 뻔한 것들" 1번의 응급처치였는데,
정확성이 **두 함수 사이의 간격**에 걸려 있었다 — ready 큐 FIFO 순서와 "`create_task`와 `try:`
사이에 아무 문장도 추가되지 않는다"는 두 개의 보이지 않는 제약. 1·2번 리팩터가 정확히 그
구간을 건드리니 더 위험해졌다. 대신 `end_turn(turn_id, reason)`을 **멱등**으로 만들고
`run()`의 finally와 `preempt()`가 **둘 다** 부른다. 본문이 한 번도 실행되지 않은 턴은 죽인 쪽이
닫는다. 타이밍 가정이 사라졌다.

나머지는 국소 정리다: 발화 경계마다 `synthesizing` 페이즈가 켜져 얼굴 mood가
`talking → thinking → talking`으로 튀며 눈썹 트랜지션을 두 번 리페인트하던 것(합성이 이미
끝났으면 알리지 않는다), `held`를 합성 await **뒤에도** 검사하지 않아 마이크가 열린 채 재생될
수 있던 창(그리고 `set_hold(true)`를 권한 await 앞으로 옮겼다), 재접속 시 `stream_hello`가
`last_seq`를 리셋하지 않아 백엔드 재시작 후 클라이언트가 모든 이벤트를 조용히 버리던 것,
타임라인 무한 증가(면(`#/face`)에서는 렌더도 안 되는데 매 이벤트가 배열을 복사했다), 게이트웨이
stderr 한 줄이 최대 4MiB까지 잡힐 수 있던 것.

`stream_hello`의 역할도 이 과정에서 정리됐다. 전송이 끊길 때마다 턴 상태를 비우면, 재접속이
**재생 경로**를 타는 경우(=hello가 안 오는 경우) 진행 중인 턴의 남은 utterance를 전부
"지난 턴"으로 버려서 **기기가 턴 중간에 벙어리가 된다** — 링 버퍼로 막으려던 실패를 다른
문으로 다시 만드는 셈이었다. 이제 끊김은 턴 상태를 건드리지 않고, **`stream_hello`가 재접속 시
턴 생존의 유일한 권위**다(서버가 우리를 이어줄 수 없을 때만 오니까).

## 검증

### 유닛 — 147개 통과 (기존 78 → 147)

backend 59 / agent-gateway 19 / voice 5 / tts 64. voice와 tts는 손대지 않았고 회귀 없다.
기존 게이트웨이 테스트 6개는 **무변경으로 통과**한다(JSON 경로가 안 바뀌었다는 증거).

`turn_started` 하나당 `turn_ended` 정확히 하나를 네 가지 실패 모양(정상/잘림/빈 응답/에러
청크)에 대해 고정했다. 와이어 JSON 키를 이벤트 종류별로 고정하는 테스트도 넣었다 —
`event_stream.ts`가 같은 이름을 손으로 적으므로 이게 드리프트를 잡는 장치다.

### 실기 — 진짜 claude, curl만으로

```
stream_hello     {'active_turn': None, 'protocol_version': 1}
turn_started     {'source': 'agent', 'trigger_text': None, ...}      ← 대기 요청 없음
utterance        {'text': '일곱 시예요.', 'seq': 0}
utterance        {'text': '일어날 시간입니다.', 'seq': 1}
turn_ended       {'reason': 'completed'}
turn_started     {'source': 'user', 'trigger_text': '자기소개를 정확히 두 문장으로 해줘.'}
utterance        {'text': '안녕하세요, 저는 …', 'seq': 0}
utterance        {'text': '코드 작업부터 …', 'seq': 1}
turn_ended       {'reason': 'completed'}
```

멀티턴(툴 사용)도 정확히 원하는 모양으로 나왔다.

```
utterance  'probe 디렉터리를 찾아서 그 안의 파일 개수를 세어볼게요.'   ← 중간보고
notice     'Glob' (tool_use)                                        ← 말하지 않음
utterance  'probe 디렉터리에는 파일이 2개 있어요.'                     ← 결론
turn_ended completed
```

**취소**: 긴 턴을 시작하고 취소했더니 `claude.exe` 프로세스 수가 14 → **13**(기준선)으로
돌아왔다. 백엔드 → 게이트웨이 → 서브프로세스로 disconnect가 전파돼 실제로 죽는다.

**재생**: `Last-Event-ID: 13`으로 재접속하면 14~16만 재생하고 hello를 보내지 않는다. 헤더
없이 접속하면 hello만 오고 **재생이 없다**(새로고침이 지난 턴을 다시 말하지 않는다).

**프록시**: `https://localhost:5176/api/events`로도 같은 게 동작한다. `retry:`와 `stream_hello`가
push 이전에 도착하므로 vite 프록시가 SSE를 버퍼링하지 않는다.

### 프론트 — 헤드리스 Chrome(CDP)으로 실제 재생까지

playwright 없이 Node 22의 전역 `WebSocket`으로 CDP를 직접 물려서 확인했다.

- 로드 직후 `stream_dot stream_open`, 타임라인에 `스트림 연결 (v1)`.
- `/api/push` → `턴 시작 (agent)` → agent 버블 2개 → `턴 종료 (completed)`.
  **앞선 user 버블이 없는 것 자체가 비요청 턴의 시각 신호다.**
- 오디오가 실제로 났다: `src`가 `blob:`, `paused: false`, `currentTime 0.98 / duration 1.50`,
  램프 "말하는 중" → 끝나고 "대기 중".
- **직렬 큐**: 세 문장을 한 번에 넣으면 서로 다른 blob 3개가 순서대로 재생된다.
  `0→1.75`, `0.03→1.83`, `0.02→1.72`. **겹침 없음.** 발화 사이 간격 ~90ms —
  이게 `speech_pending`으로 자막을 붙들어야 하는 그 창이다(계획에서 50~150ms로 예상한 값).
- **barge-in**: 재생 중 마이크 탭 → `paused: true`, 램프 "듣고 있어요", 버튼 "정지".
- 페이지 예외 0건. 네트워크 실패는 `/favicon.ico` 404(원래 없다)와 새로고침 시 SSE
  `ERR_ABORTED`뿐.

`--autoplay-policy=no-user-gesture-required`로 띄웠다. 이건 **Pi 키오스크에 필요한 바로 그
플래그**라서, 이 테스트가 예약 발화의 키오스크 경로도 같이 확인해 준 셈이다.

## 남은 확인 (수동, 실제 디바이스)

헤드리스 Chrome이 확인해 줄 수 없는 것들만 남았다. 전부 **iPhone Chrome + LAN**이 필요하다.

1. **첫 탭이 오디오를 언락하는지.** iOS의 제스처 규칙은 헤드리스에 없다.
2. **턴 중간에 `#/face` ↔ `#/` 왕복해도 소리가 유지되는지** (child-count 함정).
3. **한 번도 탭하지 않은 상태에서 예약 턴** → `탭하면 들려드릴게요`가 뜨고 다음 탭에 말하는지.
   iOS에서 자동재생 거부는 실제로 일어난다.
4. **알림이 큐에 있는 동안 녹음**해도 빈 녹음이 안 나오는지 (`set_hold`, day-4 iOS 충돌).
5. **object URL이 누적되지 않는지** (개발자도구 메모리).
6. **Pi 실기**: 키오스크 부팅 + 자동재생 플래그 + 프레임 예산. 턴당 리렌더가 대략 3배로
   늘었으므로 `notice`가 수다스러울 때 Pi에서 아픈지 봐야 한다.
7. **kill된 턴 뒤의 `--resume`**: 취소하면 assistant 응답 없는 user 메시지가 트랜스크립트에
   남는다. resume이 견디는지 확인하지 않았다. 못 견디면 취소 후 세션을 버려야 하고(한 줄)
   대화 컨텍스트를 잃는 실질 비용이 있다.

## 미룬 개선 (deferred)

### 1. 진짜 예약 잡 스토어 / 의도 추출

**결정**: 스케줄링 책임은 에이전트다. 기기는 채널만 노출한다. 우리가 잡 스토어를 만들면
시스템의 잘못된 절반을 만들고 나중에 지워야 할 위험이 있다.

**스탠드인의 한계는 정직하게 남는다**: `claude -p`는 턴 사이에 죽는 일회성 서브프로세스라
**타이머를 들고 있을 수 없다.** 아키텍처의 한계가 아니라 스탠드인의 한계다. 스탠드인이
예약하려면 턴 도중에 OS(`at`/cron/`schtasks`)에 위임해야 하고, 그러려면 `CLAUDE_ALLOWED_TOOLS`를
열어야 한다(위 "조용히 깨질 뻔한 것들" 2번을 먼저 읽을 것). 진짜 nullclaw는 상주라 이 제약이
없다.

**착수 지점**: 그래도 백엔드가 들어야 한다면 `backend/app/schedule.py`에
`turn_runner.start_prompt`를 부르는 루프 하나 + 영속화. **캐치업 정책**(꺼져 있는 동안 지난
알림: 늦게 발화 vs 폐기)을 먼저 정해야 한다 — 둘 다 방어 가능하고 잘못 고르면 사용자에게
보이는 버그다.

### 2. `--include-partial-messages`

**현재**: 블록 단위. 단일 답변의 첫 소리는 0.6초만 당겨진다(실측).

**왜 안 했나**: 완성된 `assistant` 메시지가 델타 뒤에 **또** 오므로 그대로 흘리면 **이중
발화**가 조용히 생긴다. 그리고 day-7의 이득은 지연이 아니라 구조다.

**착수 지점**: 게이트웨이 `stream()`에 content-block index별 누적 버퍼 + 문장 경계 컷. 백엔드는
무변경(§분업이 그래서 있다). **착수 조건**: "긴 단일 답변의 첫 소리까지 시간"이 실제로 신고될
때. 1회 측정으로 판단하지 말 것(`agent-latency-notes` §3).

### 3. 턴 큐 (지금은 무조건 선점)

**왜 안 했나**: 순서·staleness·깊이 제한이 전부 딸려 온다.

**현재 거친 부분 둘**:
- 예약 턴이 사용자 답변을 끊는다(그리고 그 반대도). 알람이라면 맞는 동작이지만 판단이다.
  리뷰 정리 1번 이후로는 남은 오디오까지 확실히 끊긴다.
- **에이전트가 자기 턴 도중 `/api/push`를 부르면 자기를 취소한다.** 시끄럽게 실패하지만
  (`turn_ended{cancelled}`) 혼란스럽다. 시스템 프롬프트가 막지만 **계약이 막는 게 아니다.**

**착수 지점**: `turn_runner`의 활성 턴을 `deque`로.

### 4. 다중 구독자 역할 분리

**현재**: 키오스크와 폰이 둘 다 `utterance`를 받아 **둘 다 말한다.** 버그가 아니라 실제
브로드캐스트지만 방에서 소리가 겹친다. Pi 테스트 중엔 폰을 음소거해야 한다.
**착수 지점**: `GET /api/events?role=viewer|speaker`.

### 5. `/api/events` 인증

**현재**: LAN의 누구나 대화를 엿볼 수 있다. `/api/push`만 토큰이 있다.
**착수 지점**: 같은 토큰을 쿼리 파라미터로 — `EventSource`는 헤더를 못 붙이므로 별도 고민이
필요하다. 그게 이번에 안 한 이유다.

### 6. STT를 트리거 엔드포인트로

**현재**: 프론트가 `:8100`에 직접 POST하고 텍스트로 `/api/turns`를 부른다.
**왜 안 했나**: 전사 텍스트는 user 버블을 그리려 클라이언트에 어차피 필요하고, 상태머신 재배선
대비 얻는 게 없다. **다만 더 나은 장기 모양이긴 하다** — 모든 트리거가 문 하나로 들어오고
전사가 스트림에 공짜로 실린다. **착수 지점**: `POST /api/turns/speech`(multipart) 순수 추가.
`turn_runner` 무변경.

### 7. 오디오를 이벤트 스트림에 싣기

**왜 안 했나**: 발화당 ~800KB base64가 SSE로 흐르고, `/tts` 프록시 타깃만 바꿔 melo/piper를
A/B 하는 day-4/6 설계가 깨진다. **착수 지점**: `utterance`에 payload가 아니라 `audio_url` 필드.

### 8. 이벤트 로그 영속화 / 한 문장이 너무 길 때

링 버퍼는 메모리라 재시작하면 사라진다(`stream_hello`가 그 경우를 덮는다). 착수 지점:
`event_bus`에 append-only JSONL.

`split_utterances`에 최대 길이 제한이 없다. 한 문장이 200자면 TTS 한 번에 간다. 착수 지점:
길이 인자 추가(기본값 금지 — 호출부에서 env로).

### 9. `stdin` 발견의 후속

`agent-latency-notes`의 "순수 프로세스 spawn/init 2.3~2.6초"를 **다시 재야 한다.** 그 값이
사실 이 stdin 대기였을 가능성이 있고, 그렇다면 그 문서의 결론 일부가 바뀐다. 착수 지점:
§5의 런북을 `stdin=DEVNULL`이 들어간 지금 코드로 1번부터.

### 10. day-6/day-5에서 넘어온 것들

영어→한글 치환 사전, `length_scale` 튜닝, `word2ph` 음절 단위 자막, 전송량(Opus),
`vite.config.js`를 추적에서 빼기. 이번엔 모든 경로를 `/api` 아래 둬서 프록시 함정을
**회피**했을 뿐 근본은 그대로다.

`stop_speaking`이 object URL을 revoke하지 않던 day-5 부채는 **닫혔다.** 큐가 생성한 URL마다
revoke 정확히 한 번을 보장하고, 기존 단일 슬롯 `tts_url_ref`가 다룰 수 없던 케이스(합성이
버려진 뒤에 도착)까지 덮는다.
