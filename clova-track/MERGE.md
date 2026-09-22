# 병합 기록 — super-clova → my-talking-claw/clova-track

## 무엇을
독립 프로젝트 **super-clova**(네이버 클로바 스피커 리버싱·개조)를 my-talking-claw 안의 서브트랙 **`clova-track/`**로 병합.

## 언제
2026-09-23.

## 어디서 왔나
- 원본 저장소: `scratch` (github.com/wonhotoss/scratch), 경로 `super-clova/`.
- 원본 최종 내용 커밋: `2003842` ("super-clova: 클로바 분해·리버싱 인터넷 자료 조사 — 프렌즈 chip-off 논문으로 안드로이드/8GB eMMC 확정").
- 이관 후 scratch에서는 **졸업(폴더 제거)**. [[scratch-graduation-rule]] 절차대로 원격 미부착, 푸시는 사용자.

## 왜 병합했나
이번 조사에서 **super-clova의 엔드스테이트1(루팅한 클로바에 내 어시스턴트 APK 탑재)이 my-talking-claw의 음성 프론트엔드와 동일 구조**임이 드러남. 폰(STT/TTS)↔텍스트 백엔드↔에이전트 구조에서 폰 자리에 루팅 클로바를 넣고 얼굴은 RPi 화면에 그리면 그대로 맞물린다. `voice`/`tts`/`backend`/`agent-gateway`를 공유할 수 있어, 별도 프로젝트로 둘 이유가 사라졌다. 클로바 = my-talking-claw 음성 I/O의 **대체 하드웨어 후보 트랙**으로 재배치.

## 옮긴 파일 (scratch/super-clova/*.md → clova-track/)
| 파일 | 내용 |
|---|---|
| README.md | 트랙 개요·상태·두 트랙(비파괴/분해) |
| 조사결과.md | 클로바 EOL 타임라인, CIC/CEK 아키텍처, 하드웨어=안드로이드, 재사용 자산, 출처 |
| 경로분석.md | 원안 A/B/C → 안드로이드 사실로 재편, 엔드스테이트·난이도 비교 |
| 발판확보-런북.md | 분해 트랙 손작업 절차: SoC 식별→UART/BootROM→덤프→루팅 (단계 0~5) |
| 비파괴-네트워크경로.md | 비파괴 트랙: 공유기단 캡처·능동 MITM 검토, 평문 OTA가 유일 비파괴 발판 |
| 진행기록.md | 경과·결정 이력 (2026-08-15) |
| 분해자료-조사.md | 프렌즈 chip-off 학술논문·유튜브 분해·인증서주입 논문·라파 개조 선례 (2026-09-12) |

## 병합 시 손본 것 (적당히 수정)
- `clova-track/README.md`: 상단에 병합 안내 배너 추가(트랙이 대기 상태이며 ReSpeaker 본선 뒤 병렬로 착수함을 명시). 코드네임 super-clova 유지.
- `../음성IO-로드맵.md`: 클로바 관련 절차의 파일 경로를 `D:\projects\scratch\super-clova\...`에서 `clova-track/...`로 갱신. 2026-09-23 최종 아키텍처 결정 절 추가.
- 문서 간 상대 링크(README↔조사결과↔경로분석 등)는 같은 폴더 안이라 그대로 유효.

## 이 트랙의 현재 위치
**대기(paused).** my-talking-claw 본선은 ReSpeaker Lite(USB-RPi) 경로로 진행. 클로바 트랙은 로드맵([../음성IO-로드맵.md](../음성IO-로드맵.md)) **2·3단계** — 본선이 성숙한 뒤, **발판확보-런북 단계 1.5(비파괴 BootROM 지문)** 를 선행하고 착수. 현재 막힌 지점은 그대로: 프렌즈 정확한 SoC 미확정, 발판 미확보.
