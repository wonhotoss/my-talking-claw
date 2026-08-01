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

백엔드는 사용자 입력을 아래 **에이전트 게이트웨이**로 위임한다. 환경변수 `AGENT_GATEWAY_URL`(기본 `http://127.0.0.1:3000`)과 `AGENT_PAIRING_CODE`(기본 `000000`)로 가리키며, 게이트웨이가 없으면 `/api/message`는 502를 반환한다.

### 에이전트 게이트웨이 (스탠드인)

자율형 에이전트 **nullclaw의 gateway 계약**(`POST /pair`로 6자리 코드→bearer 토큰, `POST /webhook {"message": …}`, `GET /health`)을 그대로 구현한 스탠드인 서비스다. 이 PC엔 nullclaw가 없으므로 두뇌는 **헤드리스 Claude Code**(`claude -p`)가 대신한다. 프로덕션에선 이 서비스를 실제 nullclaw로 바꾸고 백엔드의 `AGENT_GATEWAY_URL`만 그쪽으로 돌리면 된다(백엔드 코드 무변경).

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

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `AGENT_PAIRING_CODE` | `000000` | 백엔드와 공유하는 페어링 코드. |
| `CLAUDE_BIN` | `claude` | claude 실행 파일(Linux는 PATH의 `claude`). Windows는 `.cmd`/`.ps1` 심이 아닌 실제 exe 경로로: `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`. |
| `CLAUDE_MODEL` | `sonnet` | 두뇌 모델(`sonnet`/`opus`/`haiku`). |
| `CLAUDE_ALLOWED_TOOLS` | (없음) | 비우면 대화형(무툴). `Read,Glob,Grep` 등으로 자율 동작 확장. |
| `CLAUDE_SYSTEM_PROMPT` | (음성 비서 기본) | 짧은 한국어 구어체 응답 유도. |
| `CLAUDE_WORKDIR` | (cwd) | claude 실행 디렉터리. |

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

프론트를 한쪽으로 붙이려면 `frontend/vite.config.ts`의 `/tts` proxy 타깃을 8200/8201 중 하나로 바꾼다. 그 외에는 아무것도 바뀌지 않는다 — 두 엔진을 나란히 띄워 A/B로 비교할 수 있는 게 이 구조의 목적이다.

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

프론트엔드 개발 서버는 HTTPS로 실행되고, `/api`와 `/health` 요청을 로컬 백엔드 `http://127.0.0.1:8000`으로 프록시한다.

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
chromium --kiosk --ignore-certificate-errors https://<host>:5173/#/face
```

화면 아무 데나 누르면 녹음이 시작/정지된다(마이크 버튼과 동일 동작). 오른쪽 위의 **콘솔**
버튼으로 돌아온다.

표정은 대기 / 듣는 중 / 생각 중 / 말하는 중 / 마이크 거부의 다섯 가지이고, 말하는 중에는
`/tts/speak`가 준 viseme 타임라인을 따라 입모양이 바뀐다.

입 아래에는 현재 상태가 표시된다 — 대기 중 / 듣고 있어요 / 받아쓰는 중 / 생각하는 중 /
목소리 만드는 중. 말할 때는 같은 자리에 말하는 문장이 **노래방 자막**처럼 뜨고, 실제 발화
속도에 맞춰 하이라이트가 쓸려간다.

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
