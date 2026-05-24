# Day 1: Milestone 1 진행 기록

## 목표

스마트폰 브라우저를 음성 입출력 장치로 사용하고, 데스크탑에서 실행되는 백엔드가 mock 에이전트 응답을 반환하는 첫 번째 end-to-end 흐름을 만든다.

## 구현한 것

### 백엔드

- `backend/`에 uv 기반 FastAPI 프로젝트를 구성했다.
- `GET /health`를 추가했다.
- `POST /api/message`를 추가했다.
- 실제 에이전트 대신 deterministic mock 응답을 반환한다.
- FastAPI 테스트를 추가했다.

### 프론트엔드

- `frontend/`에 React, TypeScript, Vite 앱을 구성했다.
- 스마트폰에서 사용할 단일 화면을 만들었다.
- Web Speech API 기반 STT/TTS 흐름을 구현했다.
- STT 결과를 백엔드로 보내고 mock 응답을 TTS로 재생한다.
- STT/TTS, 보안 컨텍스트, 마이크 API 상태를 확인하는 진단 UI를 추가했다.
- STT 실패 후에도 마이크 입력 자체를 확인할 수 있도록 `getUserMedia`, `MediaRecorder`, `AudioContext` 기반 마이크 진단을 추가했다.

### 개발 서버

- Vite dev server를 HTTPS로 실행하도록 구성했다.
- 프론트엔드의 `/api`, `/health` 요청은 Vite proxy를 통해 로컬 FastAPI 백엔드로 전달한다.
- 스마트폰 접속은 같은 네트워크의 데스크탑 LAN IP를 사용한다.

```text
https://<desktop-lan-ip>:5173/
```

## 확인한 브라우저 동작

### iPhone Safari

Safari에서는 STT가 성공했다.

관찰된 진단 상태:

```text
SpeechRecognition: no
webkitSpeechRecognition: yes
recognition source: webkitSpeechRecognition
secureContext: yes
mediaDevices: yes
last STT error: none
```

해석:

- Safari는 표준 `SpeechRecognition` 이름을 노출하지 않아도 prefixed `webkitSpeechRecognition`으로 STT가 동작할 수 있다.
- `SpeechRecognition: no` 자체는 실패가 아니다.
- Safari에서는 Milestone 1의 브라우저 내장 STT 경로를 사용할 수 있다.

### iPhone Chrome

Chrome iOS에서는 권한을 켜면 마이크 허용 토스트가 나오고 STT 세션이 시작되는 것처럼 보이지만 곧바로 중단된다.

관찰된 진단 상태:

```text
SpeechRecognition: no
webkitSpeechRecognition: yes
recognition source: webkitSpeechRecognition
secureContext: yes
mediaDevices: yes
MediaRecorder: yes
AudioContext: yes
last STT error: aborted
```

해석:

- Chrome iOS도 `webkitSpeechRecognition`을 노출한다.
- 하지만 API 존재 여부와 실제 STT 성공 여부는 다르다.
- Chrome iOS에서는 Web Speech STT 세션이 런타임에서 `aborted`로 중단된다.
- Safari에서 같은 앱이 성공하므로 앱, 백엔드, HTTPS 구성 문제가 아니라 Chrome iOS의 Web Speech 처리 경로 문제로 본다.
- Chrome iOS에서는 브라우저 내장 STT를 신뢰하지 않는 것이 좋다.

## 진단 UI에서 얻은 결론

- `SpeechRecognition: no`, `webkitSpeechRecognition: yes`는 iPhone Safari와 Chrome iOS 모두에서 나올 수 있다.
- 실제 STT 가능 여부는 `last STT error`와 이벤트 흐름까지 봐야 한다.
- Safari의 `last STT error: none`은 내장 STT 사용 가능 상태다.
- Chrome iOS의 `last STT error: aborted`는 API는 노출되지만 내장 STT가 런타임에서 실패하는 상태다.
- `mediaDevices`, `MediaRecorder`, `AudioContext`가 모두 `yes`이면 외부 STT로 전환하기 위한 마이크 입력 경로는 확보된 상태다.

## 현재 결론

Milestone 1의 목표였던 스마트폰 음성 입출력 MVP는 Safari 기준으로 확인됐다.

Chrome iOS까지 포함해 안정적으로 음성 입력을 지원하려면 Web Speech API에만 의존하면 안 된다. 다음 단계에서는 `getUserMedia`로 마이크 입력을 확보하고, 백엔드 또는 외부 서비스에서 STT를 수행하는 경로가 필요하다.

## 다음 작업 후보

- `POST /api/transcribe` 엔드포인트 추가
- 프론트엔드에서 녹음 오디오를 백엔드로 업로드
- 외부 STT 서비스 또는 로컬 STT 엔진 연결
- Safari에서는 Web Speech STT를 우선 사용하고, Chrome iOS에서는 외부 STT 경로를 사용하는 브라우저별 전략 구성
- STT 실패 시 자동 fallback 정책 정리

## 검증 결과

백엔드 테스트:

```text
uv run pytest -q
3 passed
```

프론트엔드 빌드:

```text
npm run build
success
```
