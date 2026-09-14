# 음성 입출력 하드웨어 로드맵 — ReSpeaker 우선, 클로바 후속

3단계 계획. **ReSpeaker Lite로 프로젝트를 지금 진행**하고, 성숙 후 **이미 보유한 클로바 프렌즈로 격상**을 시도한다. 클로바 경로는 리버싱 리스크가 있어 my-talking-claw의 진도를 여기에 묶지 않는다.

## 왜 이 순서인가
- **ReSpeaker Lite** = USB UAC2 plug&play + XU316 하드웨어 AEC. 약 5만원, 당일 동작 → 프로젝트 즉시 언블록. ([hardware-notes.md](hardware-notes.md) 확정 BOM)
- **클로바 프렌즈** = 안드로이드 통합기(마이크 2개[귀] + 10W 스피커, 재생 reference가 기기 내부에 있어 barge-in 구조적 성립). 이미 보유 = 0원, 스피커 더 좋음. **그러나** USB 오디오 장치가 아니고 adb도 불가 → 루팅 + Wi-Fi 오디오 스트리밍 앱이 선결 = super-clova 전체를 푸는 일(현재 SoC 미확정·발판 미확보로 막힘, 실패 위험·수 주 소요).
- 그래서 ReSpeaker가 baseline, 클로바는 병렬 업그레이드 트랙.

## 단계 1 — ReSpeaker Lite로 최대한 진행 (지금)
- BOM: ReSpeaker Lite + 소형 4Ω 3~5W 스피커, 약 5만원.
- RPi4 + Piper TTS + openWakeWord("누구야") + faster-whisper로 음성 루프 완성. 얼굴은 RPi 화면(Chromium 키오스크 `#/face`).
- 이 단계에서 전체 아키텍처(voice/tts/backend/agent-gateway + 얼굴)를 실사용까지 끌어올린다. barge-in ducking 로직도 여기서 검증.

## 단계 2 — 프로젝트 성숙 후 클로바 시도 (비파괴 확인 선행)
- **트리거:** 음성 루프가 ReSpeaker로 안정 동작하고, 프론트엔드를 교체할 여력이 생겼을 때.
- **먼저 비파괴 확인** — super-clova 런북 **단계 1.5(BootROM 지문)**: 전원 인가 상태로 외부 USB를 PC에 꽂아 VID:PID 열거 확인(`05c6:9008`=Qualcomm EDL 등). 열거되면 **안 열고도** 덤프/플래시 가능. 아무것도 안 뜨면 외부 포트 데이터 없음 확정 → 단계 3.
- **목표 구조:** 루팅한 클로바가 지금의 "폰" 자리(웨이크워드 + 오디오 캡처/재생, Wi-Fi로 백엔드와 통신), 얼굴은 RPi 화면. = super-clova **엔드스테이트1**. `voice`/`tts`/`backend`/`agent-gateway` 그대로 공유.
- barge-in은 안드로이드 `AcousticEchoCanceler` API로 확보 시도(프렌즈=안드로이드 확정, chip-off 논문 근거).

## 단계 3 — 필요하면 열고 루팅
- 단계 1.5에서 소득이 없거나 더 깊은 접근이 필요하면 케이스 개방.
- super-clova 런북 순서: **열기**(SoC 칩 마킹 판독 — 프렌즈 SoC는 공개 자료 없음) → **발판**(기판 내부 UART 콘솔 또는 EDL test point, USB-TTL/소더링) → **루팅**(부트로더 언락+Magisk / 구버전 익스플로잇 / EDL 오프라인 주입) → **내 어시스턴트 앱 상시 실행**.
- 주의: 분해 조사에서 나온 chip-off(플래시 적출)는 **파괴적 읽기**라 장악용이 아니다. 기기를 살려 두는 **비파괴 UART/EDL** 경로로 간다.

## 트레이드오프 (명시)
- 클로바 채택 시 **클로바 + RPi = 2기기** → my-talking-claw의 "RPi 단일 기기 통합" 목표와 상충. 얼굴을 RPi에 두기로 한 이상 이 분리는 수용.
- 클로바 EOL(네이버 클라우드 종료)은 리퍼포즈엔 무해 — 자체 스택으로 대체하므로 오히려 사장될 하드웨어를 재활용하는 셈.

## 관련 문서
- 하드웨어 비교·확정 BOM: [hardware-notes.md](hardware-notes.md)
- 기기·TTS 엔진 결정: [platform-notes.md](platform-notes.md)
- super-clova 발판/루팅 절차: `D:\projects\scratch\super-clova\발판확보-런북.md`, `경로분석.md`, `분해자료-조사.md`
