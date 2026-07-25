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
cd voice
uv run pytest
```

```powershell
cd frontend
npm run build
```
