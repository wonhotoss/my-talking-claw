# My Talking Claw

스마트폰 브라우저의 STT/TTS를 사용해 텍스트 기반 에이전트와 대화하기 위한 음성 인터페이스 MVP다.

## Milestone 1 실행

### 백엔드

```powershell
cd backend
uv sync
uv run uvicorn app.server:app --host 0.0.0.0 --port 8000
```

상태 확인:

```powershell
curl http://localhost:8000/health
```

백엔드는 사용자 입력을 아래 **에이전트 게이트웨이**로 위임한다. 환경변수 `AGENT_GATEWAY_URL`(기본 `http://127.0.0.1:3000`)과 `AGENT_PAIRING_CODE`(기본 `000000`)로 가리킨다.

day-7부터 프론트-백 계약은 **단일요청-단일응답이 아니다.** 클라이언트는 오래 열린 이벤트 스트림 하나를 구독하고, 트리거가 턴을 만들고, 턴이 이벤트 연속을 낳는다. 서버는 대기 중인 요청 없이도 말할 수 있다 → [이벤트 계약](#이벤트-계약-day-7) 절.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `AGENT_GATEWAY_URL` | `http://127.0.0.1:3000` | 게이트웨이 위치. |
| `AGENT_PAIRING_CODE` | `000000` | 게이트웨이와 공유하는 페어링 코드. |
| `AGENT_BEARER_TOKEN` | (없음) | 주면 페어링을 건너뛴다. |
| `PUSH_TOKEN` | (없음) | `POST /api/push`의 토큰. **비우면 `/api/push`는 503**(조용히 받지 않는다). |
| `TURN_TIMEOUT_SECONDS` | `300` | 턴 하나의 월클럭 예산. 넘으면 취소하고 `turn_ended{failed}`. |

### 에이전트 게이트웨이 (스탠드인)

자율형 에이전트 **nullclaw의 gateway 계약**(`POST /pair`로 6자리 코드→bearer 토큰, `POST /webhook {"message": …}`, `GET /health`)을 그대로 구현한 스탠드인 서비스다. 이 PC엔 nullclaw가 없으므로 두뇌는 **헤드리스 Claude Code**(`claude -p`)가 대신한다. 프로덕션에선 이 서비스를 실제 nullclaw로 바꾸고 백엔드의 `AGENT_GATEWAY_URL`만 그쪽으로 돌리면 된다(백엔드 코드 무변경).

이 홉은 day-6에서 TTS를 Piper로 바꾼 뒤 **턴의 병목**이 됐다. 홉 분해 실측, 이미 기각된 가설, 그리고 지연 신고가 들어왔을 때의 조사 순서는 [agent-latency-notes.md](agent-latency-notes.md)에 있다.

`claude` CLI가 필요하다(기존 Claude Code 구독 인증을 재사용, 별도 API 키 불필요). 없으면 설치한다.

```powershell
npm i -g @anthropic-ai/claude-code
```

실행한다. 게이트웨이는 claude 실행 시 중첩 표시 환경변수(`CLAUDECODE`/`CLAUDE_CODE_*`)를 벗겨 Claude Code 세션 안에서도 동작하지만, 운영은 **일반 터미널** 권장이다. 인증은 기존 claude.ai 구독 로그인을 재사용한다(종량제 API 아님).

```powershell
cd agent-gateway
uv sync
$env:AGENT_PAIRING_CODE="000000"
# Windows: 실제 exe 경로 지정
$env:CLAUDE_BIN="$env:APPDATA\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
uv run uvicorn app.server:app --host 127.0.0.1 --port 3000
```

> **게이트웨이에 `--reload`를 붙이지 마라 (Windows).** uvicorn은 `--reload`(또는 `--workers`)를
> 쓰면 Windows에서 **Selector** 이벤트 루프를 고르는데, 거기서는 `asyncio.create_subprocess_exec`가
> 지원되지 않는다. 게이트웨이는 그걸로 `claude`를 띄우므로 모든 턴이 즉시 실패한다 —
> `NotImplementedError()`는 메시지가 비어 있어서 화면에는 `agent failure: NotImplementedError:`만
> 뜬다. 백엔드는 HTTP만 하니 `--reload`를 붙여도 된다. 코드를 고친 뒤에는 게이트웨이를 손으로
> 재시작해야 한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `AGENT_PAIRING_CODE` | `000000` | 백엔드와 공유하는 페어링 코드. |
| `CLAUDE_BIN` | `claude` | claude 실행 파일(Linux는 PATH의 `claude`). Windows는 `.cmd`/`.ps1` 심이 아닌 실제 exe 경로로: `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`. |
| `CLAUDE_MODEL` | `sonnet` | 두뇌 모델(`sonnet`/`opus`/`haiku`). |
| `CLAUDE_ALLOWED_TOOLS` | (없음) | **샌드박스가 아니다** — 아래 경고 참고. 자동 승인 목록일 뿐이다. |
| `CLAUDE_SYSTEM_PROMPT` | (음성 비서 기본) | 짧은 한국어 구어체 응답 유도. |
| `CLAUDE_WORKDIR` | (cwd) | claude 실행 디렉터리. |
| `DEVICE_PUSH_URL` | (없음) | 백엔드의 `/api/push`. 아래 `DEVICE_PUSH_TOKEN`과 **둘 다** 있어야 시스템 프롬프트에 아웃바운드 채널이 실린다. |
| `DEVICE_PUSH_TOKEN` | (없음) | 백엔드 `PUSH_TOKEN`과 같은 값. |

> **경고 (day-7 실측, CLI 2.1.220).** `--allowedTools`는 **권한을 제한하지 않는다.** 자동 승인 목록일 뿐이다. `CLAUDE_ALLOWED_TOOLS`를 비운 상태로 `claude -p`를 돌려도 `system/init`은 툴 33개를 전부 available로 보고하고, `Read`가 permission denial 0건으로 실제 실행된다. 즉 **지금 이 게이트웨이의 에이전트는 이미 Read/Write/Edit/Bash를 쓸 수 있다.** "비우면 대화형(무툴)"이라는 이전 설명은 틀렸다. 실제로 막으려면 `--disallowedTools`가 필요하다.

### 음성(STT) 서비스

녹음된 오디오를 텍스트로 변환하는 **독립형 온디바이스 STT 서비스**다. 에이전트 백엔드와 별개의 프로세스/포트라서 나중에 더 성능 좋은 다른 머신으로 옮길 수 있다. 첫 요청 시 Whisper 모델을 1회 내려받아 로드한다.

```powershell
cd voice
uv sync
uv run uvicorn app.server:app --host 0.0.0.0 --port 8100
```

상태 확인:

```powershell
curl http://localhost:8100/health
```

환경변수로 품질/성능을 조절한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `WHISPER_MODEL` | `base` | 가볍고 빠른 기본값. 더 높은 정확도는 `medium`, 최고는 `large-v3`. |
| `WHISPER_DEVICE` | `cpu` | GPU 머신에서는 `cuda`. |
| `WHISPER_COMPUTE_TYPE` | `int8` | GPU에서는 `float16` 권장. |
| `WHISPER_LANGUAGE` | `ko` | 요청이 언어를 지정하지 않을 때의 기본 언어. |

`base`는 가볍고 CPU에서도 빠르다. 더 높은 정확도가 필요하면 `WHISPER_MODEL=large-v3`로 실행한다(최초 약 3GB 다운로드, CPU에서는 느림).

STT 서비스를 다른 머신에서 돌리려면 프론트엔드 코드를 바꾸지 않고 `frontend/vite.config.ts`의 `/voice` proxy 타깃(운영에서는 리버스 프록시)만 그 머신 주소로 바꾼다.

### 음성(TTS) 서비스

에이전트 응답을 음성으로 합성하는 **독립형 온디바이스 TTS 서비스**다. 폰 브라우저의 내장 음성에 의존하지 않고 우리가 목소리/품질을 제어한다. **엔진이 둘**이고 같은 `/synthesize` + `/speak` 계약을 말한다.

| 엔진 | `TTS_ENGINE` | 포트 | 이미지 | RTF(4코어) | RSS | 라이선스 |
| --- | --- | --- | --- | --- | --- | --- |
| MeloTTS-Korean | `melotts` (기본) | 8200 | 5.53GB | 0.98 | 2.4GB | MIT |
| Piper `ko_KR-kss-medium` | `piper` | 8201 | 649MB | **0.057** | **252MB** | **CC BY-NC-SA 4.0** |

Piper가 **17~27배 빠르고 메모리는 1/10**이라 라즈베리파이에서 실시간이 된다. 대신 목소리가 하나뿐이고 비상업 라이선스이며, 영어 차용어 발음이 MeloTTS보다 약하다. 실측 근거와 기기별 투영은 [platform-notes.md](platform-notes.md)에 있다.

**이미지를 하나로 합치지 않는 이유**: 두 엔진의 의존성이 겹치지 않는다(MeloTTS는 torch+mecab-ko+unidic, Piper는 onnxruntime). 합치면 ~6GB를 들고 다녀야 해서 **코드는 하나, 이미지는 둘**로 간다. `app/`은 완전히 공유되고 엔진 어댑터만 `app/engines/`에서 갈린다.

```powershell
cd tts
docker compose up -d --build piper     # 또는 melo, 또는 둘 다
```

상태 확인:

```powershell
curl http://localhost:8201/health       # {"status":"ok","engine":"piper","language":"KR"}
curl http://localhost:8200/health       # {"status":"ok","engine":"melotts","language":"KR"}
```

현재 프론트는 **piper(8201)** 를 가리킨다. 바꾸려면 `/tts` proxy 타깃을 8200/8201 중 하나로 고치는데, **`frontend/vite.config.ts`와 `frontend/vite.config.js`를 둘 다** 고쳐야 한다 — 둘 다 커밋되어 있고 **Vite는 `.js`를 `.ts`보다 먼저 찾으므로 실제로 로드되는 건 `.js`다**(`.ts`만 고치면 아무 일도 일어나지 않는다). 고치면 Vite가 감지해 자동 재시작한다.

프록시 타깃 말고는 아무것도 바뀌지 않는다 — 두 엔진을 나란히 띄워 A/B로 비교할 수 있는 게 이 구조의 목적이다.

합성 테스트(WAV 저장):

```powershell
curl -s -X POST http://localhost:8200/synthesize -H "Content-Type: application/json" --data "@sample.json" -o out.wav
```

립싱크용 합성(오디오 + viseme 타임라인, JSON 저장):

```powershell
curl -s -X POST http://localhost:8200/speak -H "Content-Type: application/json" --data "@sample.json" -o speak.json
```

`POST /speak`는 `POST /synthesize`와 같은 오디오에 **입모양 타임라인**을 얹어 돌려준다.

```json
{
  "audio_base64": "<WAV bytes, base64>",
  "media_type": "audio/wav",
  "sample_rate": 44100,
  "duration": 6.75,
  "visemes": [{ "start": 0.0, "end": 0.083, "viseme": "x" }],
  "segments": [{ "start": 0.0, "end": 2.1, "text": "안녕하세요." }],
  "envelope": [0.0, 0.12, 0.31],
  "envelope_hz": 50
}
```

- `viseme`는 `a e i o u m n x` 중 하나(`m` 다문 입/양순음, `n` 중립 자음, `x` 무음). 구간은
  연속이고 마지막 `end`가 `duration`과 같다.
- `segments`는 MeloTTS가 실제로 한 번에 합성한 문장 조각과 그 오디오 구간이다(얼굴 자막용).
  조각 경계는 실측이지만 조각이 길 수 있어(최소 길이만 있고 최대가 없다) 프론트가 표시용으로
  문장 단위로 다시 쪼갠다.
- `envelope`는 0..1로 정규화한 RMS 크기. 입 벌림 정도와 자막 스윕 가중치에 쓴다.
- 오디오와 타임라인은 **반드시 한 번의 합성에서 같이** 나와야 한다. MeloTTS의 duration
  predictor는 확률적(`noise_scale_w`)이라, 따로 두 번 호출하면 음소 길이가 달라져 타임라인이
  오디오와 어긋난다. WAV가 base64로 같이 실려 오는 이유다(+33%, LAN에선 수십 ms).

환경변수 — 공통:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `TTS_ENGINE` | `melotts` | `melotts` 또는 `piper`. 이미지가 각자 자기 값을 박아둔다. |
| `TTS_DEVICE` | `cpu` | GPU 머신에서는 `cuda`. |
| `TTS_SPEED` | `1.0` | 말하기 속도. |
| `TTS_VISEME_MIN_SECONDS` | `0.04` (piper는 `0.02`) | 이보다 짧은 viseme 구간은 이웃에 흡수(깜빡임 방지). |
| `TTS_ENVELOPE_HZ` | `50` | 초당 RMS 크기 샘플 수. |

`TTS_VISEME_MIN_SECONDS`가 엔진별로 다른 건 실측 때문이다. Piper 목소리가 25% 빠르게 말해서
자음이 짧다 — `만나서 반갑습니다`의 양순음 4개가 Piper에선 23/35/35/46ms, MeloTTS에선
58/104/58/70ms다. 40ms 바닥을 그대로 두면 Piper에서 닫힌 입 4개 중 3개가 흡수돼 사라진다.

MeloTTS 전용:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `TTS_LANGUAGE` | `KR` | MeloTTS 언어. viseme 표는 한국어 전용이다. |
| `TTS_SPEAKER` | `KR` | 화자 키. |

Piper 전용:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PIPER_MODEL` | `/voices/ko_KR-kss-medium-aligned.onnx` | 정렬 출력이 패치된 음성 모델. |
| `PIPER_CONFIG` | `/voices/ko_KR-kss-medium.onnx.json` | 모델 config(패치 전 파일 옆에 그대로 있다). |

**엔진별 정렬(=viseme) 출처가 다르다.**

- MeloTTS는 `SynthesizerTrn.infer`의 어텐션 출력이라는 **내부 구현**에 의존하므로 **커밋 SHA로
  고정**되어 있다(`Dockerfile.melo`의 `melotts_commit`). 올릴 때는 의도적으로 올리고 `tts/tests`
  + 실제 `/speak` 호출을 다시 확인한다.
- Piper는 **공개 API**다. 다만 배포 모델은 오디오만 내보내므로 이미지 빌드 시
  `python -m piper.patch_voice_with_alignment`로 그래프의 `w_ceil` 텐서를 출력으로 승격시킨다
  (`Dockerfile.piper`의 voice 스테이지). 패치 안 된 모델을 물리면 `/speak`이 502로 거절한다 —
  타임라인이 조용히 사라지는 것보다 낫다.

MeloTTS는 첫 합성 시 모델을 내려받는다(compose 볼륨 `tts_hf_cache`에 캐시). Piper는 63MB 음성이
이미지에 박혀 있어 볼륨도 다운로드도 없다.

엔진을 추가하려면 `app/engines/`에 어댑터 하나(문장 조각 + 음소 심볼 + 심볼별 샘플 수)와
`app/viseme.py`에 심볼 테이블 하나를 넣고 Dockerfile을 하나 더 만든다. `app/server.py`와
`app/speech.py`(타임라인 조립·WAV·envelope)는 손댈 필요가 없다.

#### Windows에서 TTS 설치·실행 가이드

두 엔진 다 Windows에 네이티브로 안 깔린다(MeloTTS는 `mecab-ko` wheel 없음, Piper는 espeak-ng 번들이 리눅스 빌드). **Docker Desktop**으로 컨테이너를 돌린다.

1. Docker Desktop 설치 후 실행 → 트레이 고래 아이콘이 "running"이 될 때까지 대기.
2. 최초 1회 이미지 빌드. Piper는 1분 안쪽, MeloTTS는 수 분 걸린다(torch/unidic 다운로드):
   ```powershell
   cd tts
   docker compose up -d --build piper     # 649MB
   docker compose up -d --build melo      # 5.53GB
   ```
3. 이후에는 빌드 없이:
   ```powershell
   docker compose up -d piper   # 시작 (서비스명 생략하면 둘 다)
   docker compose down          # 정지
   docker compose logs -f       # 로그
   ```
4. 상태 확인: `curl http://localhost:8201/health` (piper) / `http://localhost:8200/health` (melo)
5. MeloTTS는 첫 합성 요청 때 모델을 내려받아 볼륨 `tts_hf_cache`에 캐시한다(이후 재시작에도 유지). Piper는 이미지에 이미 들어 있다.

문제 해결:

- MeloTTS 이미지가 커서 **첫 컨테이너 생성이 느릴 수 있다**(수 분). "Creating"에 한동안 머물러도 기다린다. Piper는 몇 초면 뜬다.
- Docker 데몬이 500/무응답으로 정체되면 **Docker Desktop 재시작**(또는 PowerShell `wsl --shutdown` 후 Docker Desktop 재실행) → `docker compose up -d`.
- MeloTTS 빌드는 CPU 전용 `torch/torchaudio==2.2.2`로 고정되어 CUDA 수 GB를 받지 않는다. GPU를 쓰려면 `Dockerfile.melo`의 torch 설치를 CUDA 휠로 바꾸고 `TTS_DEVICE=cuda`.
- **`Dockerfile.melo`는 라즈베리파이(arm64)에서 그대로는 빌드가 안 된다.** 고정한 `download.pytorch.org/whl/cpu` 인덱스에 torch 2.2.2 aarch64 휠이 없다(1.13.1까지만). `Dockerfile.piper`는 아무 수정 없이 빌드된다 — 자세한 건 [platform-notes.md](platform-notes.md) §4.

### 프론트엔드

```powershell
cd frontend
npm install
npm run dev
```

스마트폰에서 같은 네트워크의 데스크탑 LAN IP로 접속한다.

```text
https://<desktop-lan-ip>:5173
```

프론트엔드 개발 서버는 HTTPS로 실행되고, `/api`와 `/health` 요청을 로컬 백엔드 `http://127.0.0.1:8000`으로 프록시한다. SSE도 이 프록시를 그대로 통과한다(버퍼링 없음, day-7 실측).

마이크 권한이 계속 거부되거나 `secureContext`가 `no`이면 브라우저가 개발용 인증서를 신뢰하지 않는 상태일 수 있다. 이 경우 신뢰된 인증서, 로컬 HTTPS 터널, 또는 같은 목적의 HTTPS 개발 환경이 필요하다.

프록시를 쓰지 않고 다른 백엔드 주소를 직접 호출해야 하면 `VITE_API_BASE_URL`을 지정한다.

```powershell
$env:VITE_API_BASE_URL="https://<backend-origin>"
npm run dev
```

### 얼굴 화면 (`#/face`)

에이전트가 말할 때 음성에 맞춰 입이 움직이는 풀스크린 얼굴이다. 상단바의 **얼굴** 버튼으로
열거나 URL로 직접 들어간다.

```text
https://<desktop-lan-ip>:5173/#/face
```

해시 라우트인 이유는 최종 배포 형태가 **자체 모니터를 단 라즈베리 파이**라서다. 키오스크로
바로 부팅할 수 있다.

```bash
chromium --kiosk --ignore-certificate-errors \
  --autoplay-policy=no-user-gesture-required https://<host>:5173/#/face
```

`--autoplay-policy=no-user-gesture-required`는 **필수다.** day-7부터 에이전트가 요청 없이
먼저 말할 수 있는데(예약 알림), 무인 부팅한 키오스크에는 자동재생을 허용해 줄 탭 제스처가
없다. 이 플래그가 없으면 예약 발화가 실제 타깃 기기에서 소리를 내지 못하고 화면만
`탭하면 들려드릴게요`로 남는다.

화면 아무 데나 누르면 녹음이 시작/정지된다(마이크 버튼과 동일 동작). 오른쪽 위의 **콘솔**
버튼으로 돌아온다.

표정은 대기 / 듣는 중 / 생각 중 / 말하는 중 / 마이크 거부의 다섯 가지이고, 말하는 중에는
`/tts/speak`가 준 viseme 타임라인을 따라 입모양이 바뀐다.

입 아래에는 현재 상태가 표시된다 — 대기 중 / 듣고 있어요 / 받아쓰는 중 / 생각하는 중 /
목소리 만드는 중 / 알림이 왔어요 / 탭하면 들려드릴게요 / 서버 연결 끊김. 말할 때는 같은
자리에 말하는 문장이 **노래방 자막**처럼 뜨고, 실제 발화 속도에 맞춰 하이라이트가 쓸려간다.

day-7부터 한 턴이 문장 여러 개로 나뉘어 도착하므로, 발화 사이에 `<audio>`의 소스를 바꾸는
~90ms 창이 생긴다(실측). 그 동안 자막이 상태 라벨로 번쩍이지 않도록 마지막 자막을 유지한다.

## 이벤트 계약 (day-7)

프론트는 `GET /api/events`(SSE) 하나를 계속 열어두고, 트리거는 본문에 답이 없는 POST다. 모든 새 경로가 `/api` 아래인 건 의도적이다 — 그 프리픽스는 이미 프록시되므로 `vite.config.ts`와 **먼저 로드되는 `vite.config.js`**를 손으로 맞출 필요가 없다.

```
브라우저 ──GET  /api/events ─────────────► 계속 열림. 서버가 밀어넣는다.
        ──POST /api/turns {text, turn_id} ──► 202 {turn_id}. 본문에 답이 없다.
        ──POST /api/turns/{id}/cancel ──────► 202 {cancelled}. barge-in.
에이전트 ──POST /api/push (X-Push-Token) ──► 기기가 말한다. 대기 요청 없음.
```

### 서버 → 클라이언트 이벤트

`event:` 이름은 쓰지 않는다 — `EventSource.onmessage`는 이름 없는 이벤트만 받으므로, 이름을 쓰면 타입마다 리스너 등록이 필요하고 빠뜨리면 조용히 무동작이 된다. 핸들러 하나, `kind` 분기 하나. 일련번호는 **`id:` 라인에만** 둔다(클라이언트는 `lastEventId`로 읽고 중복을 버린다).

```
id: 412
data: {"kind":"utterance","turn_id":"t_7","seq":2,"text":"결론은 이렇습니다."}
```

| kind | 필드 | 뜻 |
| --- | --- | --- |
| `stream_hello` | `protocol_version`, `active_turn` | 접속 시 첫 이벤트. **`id:`가 없다**(연결 스코프라 시퀀스를 먹지 않는다). |
| `turn_started` | `turn_id`, `source`, `trigger_text`, `started_at` | `source`: `user`/`agent`/`external`. `trigger_text` 덕에 키오스크가 폰 사용자의 말을 본다. |
| `utterance` | `turn_id`, `seq`, `text` | **소리 내어 말할 한 덩어리.** 클라이언트가 `/tts/speak`에 던진다. |
| `notice` | `turn_id`, `notice_kind`, `text` | **보여주기만 하고 말하지 않는다.** 툴 사용·진행 상황. |
| `turn_cancelling` | `turn_id` | 프로세스가 죽기 전에 즉시 발행 → 클라이언트가 *지금* 오디오를 멈춘다. |
| `turn_ended` | `turn_id`, `reason` | `completed`/`cancelled`/`failed`. |
| `error` | `turn_id`, `message` | 턴을 끝내지 않는다. 항상 뒤에 `turn_ended{failed}`가 온다. |

`utterance` vs `notice`가 이 스키마의 핵심선이다. "조사해 보겠습니다"는 에이전트가 실제로 한 말이므로 `utterance`(말한다). 회색 상태줄 "WebSearch"는 `notice`(말하지 않는다). 클라이언트 오디오 파이프라인이 정확히 이 분기를 탄다.

**불변식**: `turn_started` 하나당 `turn_ended` 정확히 하나 — 실패·취소·타임아웃·크래시 전부 포함. `seq`는 턴별 0-based 연속. 빈 응답은 조용한 턴이 아니라 `turn_ended{failed}`다.

**재접속**: `Last-Event-ID`가 있으면 그 뒤만 재생하고 hello를 보내지 않는다. 헤더가 없으면 재생하지 않고 hello만 보낸다 — 새로고침이 지난 턴을 다시 말하면 안 되니까. 윈도우(256) 밖이면 hello로 폴백한다.

### 에이전트가 스스로 말하기 (예약작업)

`POST /api/push`는 데모용 곁가지가 아니라 **에이전트의 아웃바운드 채널**이다. 스케줄링 책임은 에이전트가 진다. 기기는 "말해라"만 노출한다.

```powershell
# 그대로 말한다. 에이전트 홉 없음, 서브프로세스 없음, 토큰 소모 없음.
curl -X POST http://127.0.0.1:8000/api/push `
  -H 'X-Push-Token: ...' -H 'Content-Type: application/json' `
  -d '{"source":"agent","prompt":null,"utterances":["일곱 시예요."]}'

# 또는 비요청 에이전트 턴 하나를 돌린다.
  -d '{"source":"agent","prompt":"사용자에게 7시라고 알려줘","utterances":null}'
```

`prompt`와 `utterances`는 **정확히 하나만** 설정해야 한다(아니면 400).

진짜 nullclaw는 자체 스케줄링 엔진을 가진 상주 에이전트라 7시에 스스로 깨어나 이걸 호출한다. 스탠드인(`claude -p`)은 턴 사이에 죽는 일회성 서브프로세스라 **타이머를 들고 있을 수 없다** — 아키텍처의 한계가 아니라 스탠드인의 한계다. 스탠드인이 예약하려면 턴 도중에 OS에 위임해야 하므로 `DEVICE_PUSH_*`와 함께 `CLAUDE_ALLOWED_TOOLS`를 열어야 한다(위 경고를 먼저 읽을 것). 리눅스 `at` 한 줄이면 된다.

> **알려진 거친 부분**: 새 트리거는 활성 턴을 선점한다(최신 의도가 이긴다). 따라서 에이전트가 **자기 턴이 도는 동안** push하면 자기 턴을 취소한다. 시끄럽게 실패하지만(`turn_ended{cancelled}`) 혼란스럽다. 시스템 프롬프트가 막지만 계약이 막는 게 아니다.

### 게이트웨이 NDJSON (nullclaw 계약 확장 제안)

`POST /webhook`에 선택적 동작 하나가 추가된다. 요청에 `Accept: application/x-ndjson`이 있으면 게이트웨이는 `Content-Type: application/x-ndjson`과 한 줄에 JSON 객체 하나씩인 청크 본문으로 응답해도 된다. 각 줄은 `{"type": "reply"|"notice"|"done"|"error", ...}`다. 구현하지 않은 게이트웨이는 헤더를 무시하고 지금과 똑같이 응답한다. 클라이언트가 **응답 Content-Type으로 분기**하므로 협상도, 버전 필드도, 설정도 없다. **문장 분할은 이 계약의 일부가 아니다** — 백엔드가 받은 텍스트를 알아서 자른다. 따라서 비스트리밍 게이트웨이가 잃는 것은 첫 문장의 *타이밍*뿐, 기능이 아니다.

발화의 입도는 백엔드와 클라이언트의 계약이고, 전달의 입도는 게이트웨이와 백엔드의 계약이다. 두 관심사, 두 손잡이, 합치지 말 것.

## STT 미지원 확인

화면에 `STT 미지원`이 표시되면 브라우저가 `SpeechRecognition` 또는 `webkitSpeechRecognition` API를 노출하지 않는 상태다. 앱의 `STT 진단` 패널에서 다음 값을 확인한다.

- `SpeechRecognition` 또는 `webkitSpeechRecognition` 중 하나가 `yes`여야 앱 내 STT를 사용할 수 있다.
- `secureContext`가 `no`이면 LAN의 `http://...` 접속이 원인일 수 있다.
- Android에서는 Chrome으로 먼저 확인한다.
- iPhone/iPad에서는 브라우저에 따라 SpeechRecognition 지원이 제한될 수 있다.

### iPhone Safari

iPhone Safari에서 다음처럼 표시되면 브라우저 내장 STT와 마이크 접근이 모두 막힌 상태다.

```text
SpeechRecognition: no
webkitSpeechRecognition: no
secureContext: no
mediaDevices: no
```

우선 `https://<desktop-lan-ip>:5173`로 접속해 `secureContext`와 `mediaDevices`를 `yes`로 만드는 것이 목표다. `SpeechRecognition`이 계속 `no`여도 `mediaDevices`가 `yes`이면 앱 버튼으로 마이크 권한 요청과 짧은 녹음 확인을 진행할 수 있다.

Safari에서 `SpeechRecognition: no`, `webkitSpeechRecognition: yes`, `recognition source: webkitSpeechRecognition`, `last STT error: none`으로 표시되면서 STT가 진행되면 정상이다. Safari는 표준 이름 대신 prefixed API를 노출할 수 있다.

`mediaDevices`가 살아난 뒤에는 Web Speech API 대신 독립형 STT 서비스를 사용한다(day 2에서 구현).

- 앱이 `getUserMedia`/`MediaRecorder`로 녹음한 오디오를 `/voice/transcribe`로 업로드한다.
- STT 서비스가 faster-whisper로 텍스트를 반환하면 기존 `/api/message` 흐름으로 이어진다.
- Web Speech STT가 동작하는 Safari는 그대로 두고, Chrome iOS·미지원 브라우저만 이 경로를 탄다.

### iPhone Chrome

iPhone Chrome도 iOS의 WebKit 기반 브라우저라 Safari와 비슷한 제약을 받는다. 마이크 버튼이 바로 꺼지면 앱의 `마이크` 패널에서 다음을 확인한다.

- `STT 오류: not-allowed`가 나오면 iOS 설정에서 Chrome의 마이크와 음성 인식 권한을 확인한다.
- 확인 경로: iPhone `설정` 앱 > `Chrome` > `Microphone` 켬, `Speech Recognition` 켬.
- Safari에서는 STT가 성공하고 Chrome에서만 `not-allowed`가 나오면 앱/백엔드 문제가 아니라 Chrome 앱 권한 또는 Chrome 사이트 권한 문제일 가능성이 높다.
- 권한을 켠 뒤 `STT 오류: aborted`가 나오면 Chrome iOS가 STT 세션을 시작했다가 결과 없이 중단한 것이다. 앱은 이 경우 자동으로 마이크 스트림 진단을 이어서 실행한다.
- `STT 진단` 이벤트가 `start`, `audiostart`, `error: aborted`, `end` 순서라면 마이크 권한은 통과했지만 Chrome의 SpeechRecognition 서비스가 중단된 것이다.
- Safari와 Chrome이 모두 `SpeechRecognition: no`, `webkitSpeechRecognition: yes`로 보여도 결과가 다를 수 있다. 이 경우 API 존재 여부가 아니라 `last STT error`를 기준으로 판단한다.
- Safari의 `last STT error: none`은 내장 STT 사용 가능 상태이고, Chrome의 `last STT error: aborted`는 내장 STT를 사용할 수 없는 런타임 실패 상태다.
- `stream granted`가 없으면 `getUserMedia` 권한 또는 보안 컨텍스트 단계에서 실패한 것이다.
- `stream granted` 뒤 `마이크 트랙 종료`가 바로 나오면 브라우저가 오디오 track을 즉시 종료한 것이다.
- `recorder stopped unexpectedly`가 나오고 `level`이 움직이면 마이크 스트림은 살아 있지만 `MediaRecorder`만 조기 종료된 것이다.
- `level`이 움직이면 녹음이 살아 있는 것이고, 정지 시 녹음 파일이 `/voice/transcribe`로 업로드되어 텍스트로 변환된다.

## 검증

```powershell
cd backend
uv run pytest
```

```powershell
cd agent-gateway
uv run pytest
```

```powershell
cd voice
uv run pytest
```

```powershell
cd tts
uv run pytest
```

```powershell
cd frontend
npm run build
```

현재 147개 통과(backend 59 / agent-gateway 19 / voice 5 / tts 64).

이벤트 계약은 curl만으로 전부 확인된다. 한 터미널에서 스트림을 열어두고,

```powershell
curl -N http://127.0.0.1:8000/api/events
```

다른 터미널에서 트리거를 넣는다.

```powershell
# 사용자 턴 (turn_id는 클라이언트가 만든다)
curl -X POST http://127.0.0.1:8000/api/turns -H 'Content-Type: application/json' `
  -d '{"text":"자기소개를 두 문장으로 해줘","turn_id":"t_manual_1"}'

# 예약작업 데모 — 대기 중인 클라이언트 요청 없이 기기가 말한다
curl -X POST http://127.0.0.1:8000/api/push -H 'X-Push-Token: ...' `
  -H 'Content-Type: application/json' `
  -d '{"source":"agent","prompt":null,"utterances":["일곱 시예요."]}'

# 취소 → 서브프로세스가 실제로 사라지는지 같이 본다
curl -X POST http://127.0.0.1:8000/api/turns/t_manual_1/cancel
```

`:8000`뿐 아니라 **`https://<host>:5173/api/...`로도** 같은 걸 돌려야 프록시가 SSE를 버퍼링하지 않는다는 게 확인된다.
