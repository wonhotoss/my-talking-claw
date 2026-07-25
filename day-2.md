# Day 2: 독립형 온디바이스 STT 서비스

## 목표

Day 1에서 우선 테스트 디바이스인 iPhone Chrome은 브라우저 내장 Web Speech STT가 런타임에 `aborted`로 실패한다는 것을 확인했다. Day 2에서는 녹음 오디오를 텍스트로 바꾸는 **독립형 STT 서비스**를 만들어 iPhone Chrome에서 실제로 음성 입력이 되게 한다.

방침: STT/TTS는 온디바이스 우선, 대화가 가능한 품질. 라즈베리파이가 감당 못 하면 별도 인프라로 옮길 수 있도록 **별도 서버로 분리**한다.

## 아키텍처

```
Phone browser (frontend, HTTPS :5173)
  ├─ /api/*   → (Vite proxy) → agent backend (:8000)   [기존, 변경 없음]
  └─ /voice/* → (Vite proxy) → voice service (:8100)   [신설]

voice service (:8100)  ← 독립 배포 단위. Vite proxy 타깃만 바꾸면 다른 머신으로 이동.
  faster-whisper (온디바이스)
```

- HTTPS 프론트에서 평문 HTTP 서비스를 직접 호출하면 mixed-content로 차단되므로, 프론트는 상대경로 `/voice/*`만 호출하고 위치는 proxy 타깃 한 곳으로 제어한다.
- 에이전트 게이트웨이(`backend/`)는 이번에 전혀 수정하지 않아 서비스 간 결합을 피했다.

## 구현한 것

### 음성(STT) 서비스 `voice/`

- uv 기반 FastAPI 프로젝트를 신설했다. 의존성: `faster-whisper`, `python-multipart`.
- `app/stt.py`: `faster-whisper` 래퍼. `WhisperModel`을 첫 요청 시 1회 lazy 로드해 재사용한다. 설정은 환경변수(`WHISPER_MODEL`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`/`WHISPER_LANGUAGE`)로 받아, 코드 변경 없이 더 성능 좋은 머신으로 옮길 수 있다.
- `app/server.py`:
  - `GET /health` → 상태와 모델/디바이스/compute_type.
  - `POST /transcribe` → multipart 오디오 업로드를 임시파일로 저장해 전사하고 텍스트를 반환한다. 빈 파일은 400, 인식된 음성이 없으면 422로 early fail 한다.
- `faster-whisper`는 PyAV(av 휠에 ffmpeg 번들)로 디코드하므로, iPhone Chrome의 `audio/mp4`(m4a)와 webm/opus를 별도 ffmpeg 설치 없이 처리한다.

### 프론트엔드

- `transcribe_audio(blob)`를 추가해 녹음 blob을 `/voice/transcribe`로 업로드한다.
- 마이크 녹음 정지 시 전사 → 사용자 텍스트 표시 → 기존 `submit_text`(→ `/api/message` → 응답 TTS) 흐름으로 자동 연결했다.
- 전사 중 상태(`전사 중`)와 버튼 비활성화를 추가했다.
- Safari(Web Speech 동작) 경로는 그대로 두고, Chrome iOS·미지원 브라우저만 서버 STT 경로를 타는 hybrid 라우팅을 유지했다.

### 개발 서버

- Vite proxy에 `/voice` → `http://127.0.0.1:8100`을 추가했다. voice 서비스는 자기 루트(`/transcribe`, `/health`)를 소유하므로, proxy에서 `/voice` 프리픽스를 `rewrite`로 벗겨서 전달한다. (에이전트 백엔드는 라우트가 `/api` 프리픽스를 포함해 별도 rewrite가 필요 없다.)

## 검증

### 서비스 단위 테스트 (모델 불필요)

`stt.transcribe`를 monkeypatch로 대체해 `/health`, `/transcribe` 계약과 빈 입력 거부를 검증했다.

```text
cd voice
uv run pytest -q
5 passed
```

### 실제 STT 경로 스모크 (데스크탑)

`base` 모델로 서비스를 띄우고, Windows SAPI(Heami, ko-KR)로 생성한 한국어 clip을 `POST /transcribe` 했다. 유닛 테스트가 건너뛰는 실제 디코드+추론 경로를 확인했다.

- 입력 발화: `안녕하세요. 오늘 서울 날씨 어때요?`
- 응답: `{"text":"안녕하세요. 오늘 서울 날씨 어때요?","language":"ko","duration_seconds":4.22}`

멀티파트 업로드 → 임시파일 저장 → faster-whisper 디코드 → 한국어 추론 → 응답 스키마까지 정상.

### 프론트 빌드 / 백엔드 회귀

```text
cd frontend; npm run build  → success
cd backend;  uv run pytest  → 3 passed (코드 미변경, 회귀 없음)
```

## 남은 확인 (수동, 실제 디바이스)

- iPhone Chrome 엔드투엔드: 폰에서 `https://<LAN-IP>:5173` 접속 → 마이크 녹음 → 전사 → 응답 TTS까지.
- 운영 품질에서는 `WHISPER_MODEL=large-v3`로 한국어 정확도 확인.

## 다음 작업 후보

- **핫워드/웨이크워드("누구야") — day 3.** 상시 청취는 모바일 브라우저 특성상 앱 화면 포그라운드에서만 가능. 엔진(STT 루프 / Picovoice Porcupine WASM / 하이브리드)을 정해 구현.
- TTS의 서버 이전: 동일한 voice service에 온디바이스 한국어 TTS(MeloTTS/Piper/XTTS 등) 추가.
- 실제 에이전트(nullclaw) 연동, 세션/대화 상태.
- 모든 브라우저를 서버 STT로 통일하는 옵션 토글.
