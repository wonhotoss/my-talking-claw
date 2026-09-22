# HANDOFF — my-talking-claw

이 세션에서 이어서 작업하기 위한 인수인계. 마지막 갱신 2026-09-23.

## 한 줄
마이크·스피커 없는 저성능 기기에 사는 자율 에이전트와 **음성으로 대화하는 인터페이스**. 현재 폰 프론트(브라우저 STT/TTS) 구성을 **RPi 단일 기기(마이크+스피커+얼굴 화면) 로 통합** 중.

## 지금 상태 (확정된 것)
- **아키텍처: USB-RPi + 자체 스택으로 확정.** turnkey(ESP32+ESPHome+Home Assistant) 경로는 **안 간다**(근거는 [음성IO-로드맵.md](음성IO-로드맵.md) 2026-09-23 절).
- **최종 기기: RPi4 4GB**(팬리스 무음). 임시 x86 벤치로 Lenovo IdeaPad S210.
- **TTS: Piper `ko_KR-kss-medium`** 로 교체 결정(MeloTTS 대비 RTF 0.98→0.057, RAM 8→4GB). viseme는 `piper.patch_voice_with_alignment`로 100% 커버·11.6ms.
- **STT: faster-whisper base**(한국어 tiny 부족). **웨이크워드: openWakeWord(RPi)** — RPi4에서 사실상 공짜.
- **마이크/스피커: ReSpeaker Lite "Voice Assistant Kit" 구매**(XU316 하드웨어 AEC, 2마이크, 스피커 동봉). 수령 후 **USB 펌웨어 플래시** 필요, 동봉 XIAO ESP32S3는 USB 모드에선 잉여.
- **얼굴: 점·선 풀스크린**(Chromium 키오스크 `#/face`), 음성에 맞춰 입모양(viseme).
- 서비스 분리 유지: `voice`(STT)·`tts`·`backend`·`agent-gateway`. 계약(`/voice/transcribe`, `/api/message`, `/speak`)은 프론트 교체와 무관하게 유지.

## 이번 세션(2026-09)에 조사·확정한 것
1. **ReSpeaker Voice Kit 식별.** AliExpress 구매품 = 보드+XIAO ESP32S3+스피커. USB 펌웨어로 플래시하면 계획대로 UAC2 사운드카드. → [음성IO-로드맵.md](음성IO-로드맵.md).
2. **turnkey 경로 기각.** STT/TTS는 MCU에 못 올려 어차피 호스트(RPi) 필요, 웨이크워드 오프로드는 이득 미미(openWakeWord가 거의 공짜), 정작 값진 AEC는 USB 모드에서 딸려옴, HA는 스마트홈 플랫폼이라 순수 음성 에이전트엔 과함, viseme 립싱크 유지하려면 자체 TTS 필요.
3. **덕킹 메커니즘 규명.** "음악 줄였다 복원"은 보드가 아니라 ESPHome 믹서+HA가 하던 것. 자체 스택에선 RPi 루프에 직접 구현(AEC가 하드웨어로 받쳐줘 소량).
4. **클로바 하드웨어 확정(분해 조사).** 프렌즈=안드로이드, Micron 8GB eMMC. → [clova-track/분해자료-조사.md](clova-track/분해자료-조사.md).

## 서브트랙: clova-track/ (구 super-clova, 2026-09-23 병합)
클로바 프렌즈를 루팅해 **음성 프론트엔드로 재활용**하는 대체 하드웨어 트랙. 본선(ReSpeaker)과 수렴하는 구조라 병합함(→ [clova-track/MERGE.md](clova-track/MERGE.md)). **현재 대기** — 로드맵 2·3단계. 착수 전 [clova-track/발판확보-런북.md](clova-track/발판확보-런북.md) **단계 1.5(비파괴 BootROM 지문)** 선행. 막힌 지점: 프렌즈 정확한 SoC 미확정·발판 미확보.

## 다음 할 일 (우선순위)
1. **TTS를 Piper로 교체.** `tts/` 엔진 교체(Dockerfile 5.53GB→605MB, torch/mecab/unidic 제거), `tts/app/tts_engine.py`를 `AudioChunk`로, `tts/app/viseme.py` 자모 테이블→IPA 43심볼, 서비스층 영어→한글 치환 사전. `/speak` 응답 계약 무변경. 상세 대응표 [platform-notes.md](platform-notes.md) §5.
2. **ReSpeaker Voice Kit 수령 시:** 보드 쪽 USB-C로 USB 펌웨어(`respeaker_lite_usb_xmos_v2.0.5.bin`) 플래시 → RPi 상시 음성 루프(캡처+openWakeWord+VAD+덕킹+재생) 구현. 동봉 스피커 임피던스 확인(4Ω).
3. **얼굴 렌더 + viseme 실기 검증**(RPi, TTS를 1코어에 묶은 상태에서 프레임 예산 — [day-5.md](day-5.md):313 미확인 항목).
4. **(후순위) 클로바 트랙** 로드맵 2단계.

## 저장소 / 커밋
- my-talking-claw 로컬 git(`main`). **원격 미부착, 푸시는 사용자가** 대화형 터미널에서.
- super-clova는 `scratch`에서 **졸업(폴더 제거)**, 이 프로젝트 `clova-track/`으로 이관 완료.

## 문서 지도
| 문서 | 내용 |
|---|---|
| [overview.md](overview.md) · [milestone-1.md](milestone-1.md) · day-1~7.md | 초기 설계·경과 |
| [hardware-notes.md](hardware-notes.md) | 마이크/스피커 하드웨어 비교·확정 BOM |
| [platform-notes.md](platform-notes.md) | RPi4 vs IdeaPad, MeloTTS→Piper 실측 |
| [음성IO-로드맵.md](음성IO-로드맵.md) | 3단계 로드맵 + 2026-09-23 최종 아키텍처 결정 |
| [clova-track/](clova-track/) | 클로바 리버싱·분해 트랙 (+ [MERGE.md](clova-track/MERGE.md)) |
