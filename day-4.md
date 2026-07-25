# Day 4: 셀프호스트 TTS 서비스 (온디바이스, 이전 가능)

## 목표

폰 브라우저 내장 `speechSynthesis`(기기 종속)를 걷어내고, STT처럼 **이전 가능한 별도 TTS 서버 + 자체(온디바이스) 모델**로 옮긴다. 사용자 선택: **셀프호스트 전용**(외부 API 미사용).

## 엔진 조사·결정

- 한국어 셀프호스트 TTS 대안 조사(품질/자원/지연/라이선스). 요지:
  - **Kokoro-82M**: 초경량·Apache지만 **모델카드(VOICES.md) 확인 결과 한국어 미지원** → 제외(블로그의 "한국어 지원" 표기는 오정보).
  - **Piper**: 공식 한국어 음성 없음 → 제외.
  - **MeloTTS-Korean(MIT)**: 한국어 전용, CPU 실시간, 가장 자연스러운 축. MeCab(python-mecab-ko)·unidic·torch 필요.
  - Chatterbox/Fish-Speech/CosyVoice2: 고품질이나 GPU 필요.
- **결정: MeloTTS-Korean**. MeCab이 Windows wheel이 없어 네이티브 설치가 막히므로 **Docker 컨테이너**로 구동 → "이전 가능한 별도 서버"에 오히려 부합(어디서든 `docker run`).

## 아키텍처

```
phone → frontend
  ├ /api/*   → backend :8000 → agent gateway :3000
  ├ /voice/* → STT service :8100 (faster-whisper)
  └ /tts/*   → TTS service :8200 (MeloTTS-Korean, Docker)   ← 신설
```

## 구현한 것

### 신규 `tts/` 서비스 (:8200)

- `app/tts_engine.py`: `melo.api.TTS`를 **lazy import**(엔진 미설치 호스트에서도 모듈 임포트 가능). `synthesize(text)` → 임시 WAV로 합성 후 바이트 반환. env `TTS_LANGUAGE`/`TTS_SPEAKER`/`TTS_DEVICE`/`TTS_SPEED`, 스왑용 `TTS_ENGINE`.
- `app/server.py`: `GET /health`(engine/language), `POST /synthesize`(JSON `{text}`) → `audio/wav` 바이트. 빈 텍스트 400, 엔진 오류 502.
- `pyproject.toml`: MeloTTS를 잠긴 의존성에 넣지 않음 → 코드/유닛테스트는 어디서나(uv sync) 실행 가능. 엔진은 Dockerfile에서 설치.
- `Dockerfile` + `docker-compose.yml`: python:3.10-slim에 MeloTTS(git) + unidic + python-mecab-ko + FastAPI. 모델은 첫 합성 시 다운로드, compose 볼륨 `tts_hf_cache`에 캐시. 포트 8200.
- `tests/test_server.py`: `synthesize` monkeypatch로 `/health`·`/synthesize` 계약 + 빈 텍스트 400 검증(엔진 로드 없이).

### 프론트엔드 [frontend/src/app.tsx](frontend/src/app.tsx)

- 브라우저 `speechSynthesis` **전면 제거**. `speak_text`가 `POST /tts/synthesize` → blob → 공유 `HTMLAudioElement` 재생으로 교체.
- **iOS 오디오 언락**: 탭 제스처에서 짧은 무음 WAV(런타임 생성)로 오디오 엘리먼트를 1회 재생해 잠금 해제(fetch 이후 재생 허용). 언락 중 이벤트는 `audio_unlocking_ref`로 억제.
- `is_speaking`(스피커 램프)은 오디오 `onPlay`/`onEnded`/`onError`로 구동, `stop_speaking`은 `audio.pause()`.

### Vite proxy

`/tts` → `http://127.0.0.1:8200`(프리픽스 rewrite), `/voice`와 동일 패턴.

### 기기(iPhone Chrome) 테스트에서 잡은 수정

- **빈 녹음 방지(마이크 세션 충돌)**: 초기엔 마이크 스트림을 계속 열어뒀는데, TTS가 재생되면 iOS가 마이크 트랙을 음소거/종료시켜 몇 턴 뒤 녹음이 빈 채로 끝났다("녹음된 오디오가 없습니다"). → **매 턴 새 스트림 획득 후 즉시 해제**(`acquire_microphone`/`release_microphone`), 녹음 시작 전 `stop_speaking`으로 오디오 세션을 먼저 비움. 권한은 로드 시 1회만 요청(이후 재프롬프트 없음).
- **빈 전사 → 422 방지**: STT의 `vad_filter=True`가 짧은/조용한(특히 TTS 직후) 발화를 통째로 걷어내 빈 결과 → 422. → `voice`에서 **VAD 기본 off**(`WHISPER_VAD` env로 토글). 422는 프론트에서 "음성이 인식되지 않았어요"로 안내.

## 검증

- **유닛(Windows, 엔진 mock)**: `cd tts; uv run pytest` → **3 passed**.
- **프론트 빌드**: `npm run build` → 성공(서버 TTS 배선 타입체크 통과).
- **실제 한국어 합성(Docker 컨테이너)**: `docker compose up -d` 후 `POST /synthesize` → **HTTP 200, 유효 WAV(44.1kHz/mono/16bit, ~6.75s)** 생성 확인("안녕하세요. 만나서 반갑습니다. 무엇을 도와드릴까요?"). 음질(자연스러움)은 사용자 청취로 확정.
  - 빌드 이슈 해결: 기본 torch가 CUDA 스택(수 GB)을 끌어와 이미지가 비대 → CPU 전용으로 전환. 이후 torch/torchaudio ABI 불일치(`undefined symbol: aoti_torch_abi_version`) → **torch/torchaudio 2.2.2 CPU 매칭 핀 + pip constraint**로 해결. 최종 이미지 ~1.45GB.
  - 이 데스크탑 Docker Desktop은 대형 이미지 컨테이너 생성이 느리고 한 번 데몬이 500으로 정체됨 → 재시작 후 정상. 라즈베리파이/Linux에선 이런 정체 없이 뜰 것으로 예상.
- **엔드투엔드(iPhone Chrome)**: 발화 → STT → 에이전트(실제 Claude) → **MeloTTS 음성** 재생까지 여러 턴 연속 정상 확인. 프록시 경로(`/api`, `/tts`)도 서버측 200 검증. 위 두 수정(빈 녹음·422) 반영 후 반복 턴에서도 안정 동작.

## 환경 특이사항

- MeloTTS는 Windows 네이티브 설치 불가(MeCab wheel 없음) → **Docker로 구동**(이 PC는 Docker Desktop 사용). 라즈베리파이/Linux는 네이티브 또는 Docker 모두 가능.
- 품질이 부족하면 `tts/app/tts_engine.py`에서 엔진 스왑(GPU 박스의 Chatterbox 등).

## 향후 과제

- **TTS 지연 개선(우선)**: 현재 녹음→응답→음성까지 지연이 크다. MeloTTS 첫 합성 시 모델 로드 + CPU 합성 + 왕복이 겹친다. 후보:
  - 컨테이너 기동 시 모델 **프리로드**(첫 요청 지연 제거) + 컨테이너 상시 유지(warm).
  - **문장 단위 스트리밍 합성/재생**으로 첫 소리까지 시간 단축.
  - 더 빠른 엔진/설정 또는 **GPU**(별도 인프라), 시스템 프롬프트로 짧은 응답 유도.
- 직결 마이크 완전 보이스 인터페이스 + VAD/핫워드(연기분).
